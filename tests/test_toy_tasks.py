import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import subprocess
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from agent_sidecar.crypto import Identity
from agent_sidecar.relay import make_handler as relay_handler
from agent_sidecar.research import ResearchStore, make_research_handler, redact
from agent_sidecar.service import Sidecar, make_handler
from agent_sidecar.task_summary import summarize, validate_summary
from agent_sidecar.toy_protocol import sign_request
from urllib.error import HTTPError
from agent_sidecar.toy_protocol import post


class ToyFixture(unittest.TestCase):
    def setUp(self):
        from agent_sidecar.store import RelayStore
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.servers = []
        self.nodes = []
        self.relay = RelayStore(self.root / "relay.db")
        self.research = ResearchStore(self.root / "research.db", "study")
        self.relay_url = self.serve(relay_handler(self.relay))
        research_url = self.serve(make_research_handler(self.research, "x" * 32))
        for i in range(4):
            directory = self.root / str(i)
            directory.mkdir()
            (directory / "toy-config.json").write_text(json.dumps({"mode": "toy", "experiment_id": "study", "research_url": research_url, "summary": {"kind": "demo"}}))
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(None))
            with patch("agent_sidecar.capabilities.detect_hardware", return_value={}):
                node = Sidecar(directory, self.relay_url, direct_endpoint=f"http://127.0.0.1:{server.server_port}")
            server.RequestHandlerClass = make_handler(node)
            self.start(server)
            node.toy.configure_profile("User " + str(i), True)
            self.nodes.append(node)
        self.a, self.b, self.c, self.d = self.nodes

    def start(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((server, thread))

    def serve(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.start(server)
        return f"http://127.0.0.1:{server.server_port}"

    def tearDown(self):
        for node in self.nodes:
            node.toy.stop()
        for server, thread in reversed(self.servers):
            server.shutdown()
            server.server_close()
            thread.join()
        for node in self.nodes:
            node.store.close()
        self.relay.close()
        self.research.db.close()
        self.temp.cleanup()

    def tick(self, count=3):
        for _ in range(count):
            for node in self.nodes:
                node.toy.sync()

    def publish(self):
        return self.a.toy.act("publish", {"title": "Research", "original": "Compare two approaches", "acceptance": "A table with sources"}, "publish-1")

    def join(self, task, node):
        node.toy.act("apply", {"task_id": task["task_id"], "terms_hash": task["terms_hash"], "proposal": "I can help"})
        self.a.toy.act("approve", {"task_id": task["task_id"], "node_id": node.identity.agent_id})
        self.tick()


class ToyTaskTests(ToyFixture):
    def test_subtask_decline_does_not_create_group(self):
        task = self.publish()
        self.join(task, self.b)
        child = self.a.toy.act("subtask", {"task_id": task["task_id"], "owner_id": self.b.identity.agent_id, "title": "Optional", "original": "Optional work"})
        self.b.toy.act("decline_subtask", {"task_id": child["task_id"]})
        self.assertEqual(self.b.toy.detail(child["task_id"])["status"], "cancelled")
        self.assertIsNone(self.b.store.project(child["project_id"]))
    def test_group_bypass_and_join_proof_tampering_are_rejected(self):
        task = self.publish()
        with self.assertRaises(Exception):
            self.a.send_project_invitation(task["project_id"], self.d.identity.agent_id)
        with self.assertRaises(Exception):
            self.a.trust_for_project(task["project_id"], self.d.identity.agent_id)
        self.join(task, self.b)
        full = self.a.toy.detail(task["task_id"])
        application = copy.deepcopy(full["applications"][self.b.identity.agent_id])
        application["approval"]["data"]["node_id"] = self.d.identity.agent_id
        with self.assertRaises(ValueError):
            self.a.toy._verify_join(full, self.b.identity.agent_id, application)

    def test_research_forgery_admin_authorization_and_dedupe(self):
        node = self.a.identity.agent_id
        consent = self.a.toy.research._consent()
        event = {"event_id": "same-event", "node_id": node, "user_id": node,
                 "experiment_id": "study", "created_at": int(time.time()), "consent_id": consent["request_id"],
                 "subjects": [node], "kind": "test", "payload": {"text": "research content"}}
        request = sign_request(self.a.identity, "research.batch", {"experiment_id": "study", "events": [event]})
        self.research.ingest(request)
        self.research.ingest(request)
        self.assertEqual(len([e for e in self.research.query()["events"] if e["event_id"] == "same-event"]), 1)
        forged = sign_request(self.b.identity, "research.batch", {"experiment_id": "study", "events": [event]})
        with self.assertRaises(ValueError):
            self.research.ingest(forged)
        with self.assertRaises(HTTPError) as error:
            post(self.a.toy.config["research_url"] + "/v1/research/query", {})
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_restart_recovers_queued_publish_and_summary_work(self):
        from agent_sidecar.toy_tasks import ToyTasks
        original = self.a.toy._request
        payload = {"title": "Queued", "original": "Task", "acceptance": "Check"}
        def lost(request):
            value = original(request)
            if request["operation"] == "publish":
                raise OSError("lost reply")
            return value
        with patch.object(self.a.toy, "_request", side_effect=lost):
            result = self.a.toy.act("publish", payload, "lost-request")
        self.assertEqual(result["status"], "queued")
        self.a.toy = ToyTasks(self.a)
        task = self.a.toy.act("publish", payload, "lost-request")
        self.assertEqual(len(self.a.toy.list()["tasks"]), 1)
        self.a.toy.run_summary()
        self.assertEqual(self.a.toy.detail(task["task_id"])["summary"]["status"], "ready")

    def test_leave_parent_revokes_children_and_owner_must_close(self):
        task = self.publish()
        self.join(task, self.b)
        self.join(task, self.c)
        child = self.a.toy.act("subtask", {"task_id": task["task_id"], "owner_id": self.b.identity.agent_id, "title": "Child", "original": "part"})
        self.b.toy.act("accept_subtask", {"task_id": child["task_id"], "terms_hash": child["terms_hash"]})
        self.b.toy.act("invite", {"task_id": child["task_id"], "node_id": self.c.identity.agent_id})
        self.c.toy.act("accept_invite", {"task_id": child["task_id"], "terms_hash": child["terms_hash"]})
        self.tick()
        with self.assertRaises(Exception):
            self.b.toy.act("leave", {"task_id": task["task_id"]})
        self.c.toy.act("leave", {"task_id": task["task_id"]})
        self.tick()
        with self.assertRaises(Exception):
            self.c.post_project_board_event(child["project_id"], "post", {"text": "not allowed"})

    def test_publish_apply_approve_group_summary_and_private_trace(self):
        task = self.publish()
        self.join(task, self.b)
        detail = self.b.toy.detail(task["task_id"])
        self.assertTrue(detail["group_ready"])
        self.assertEqual(self.b.store.trusted_ids(), [])
        self.a.toy.run_summary()
        self.assertEqual(self.b.toy.detail(task["task_id"])["summary"]["status"], "ready")
        self.b.post_project_board_event(task["project_id"], "post", {"text": "group trace content"})
        self.a.trust(self.b.identity.agent_id, self.b.identity.public().signing_key)
        self.b.trust(self.a.identity.agent_id, self.a.identity.public().signing_key)
        self.a.send_direct(self.b.identity.agent_id, "chat.message", {"text": "private trace content"})
        for node in self.nodes:
            node.toy.research.flush()
        collected = json.dumps(self.research.query()["events"])
        self.assertIn("private trace content", collected)
        self.assertIn("group trace content", collected)
        self.assertNotIn("signing_private", collected)

    def test_hierarchy_consent_visibility_and_result_review(self):
        task = self.publish()
        self.join(task, self.b)
        self.join(task, self.c)
        child = self.a.toy.act("subtask", {"task_id": task["task_id"], "owner_id": self.b.identity.agent_id, "title": "Child", "original": "Do part", "acceptance": "A result"})
        self.b.toy.act("accept_subtask", {"task_id": child["task_id"], "terms_hash": child["terms_hash"]})
        self.b.toy.act("invite", {"task_id": child["task_id"], "node_id": self.c.identity.agent_id})
        self.c.toy.act("accept_invite", {"task_id": child["task_id"], "terms_hash": child["terms_hash"]})
        self.tick()
        self.assertTrue(self.c.toy.detail(child["task_id"])["group_ready"])
        with self.assertRaises(Exception):
            self.d.toy.detail(child["task_id"])
        with self.assertRaises(Exception):
            self.a.project_board(child["project_id"])
        with self.assertRaises(Exception):
            self.b.toy.act("invite", {"task_id": child["task_id"], "node_id": self.d.identity.agent_id})
        result = self.b.toy.act("submit", {"task_id": child["task_id"], "text": "Finished"})
        sid = result["submissions"][-1]["submission_id"]
        self.a.toy.act("review", {"task_id": child["task_id"], "submission_id": sid, "decision": "accept"})
        self.assertEqual(self.b.toy.detail(child["task_id"])["status"], "completed")

    def test_idempotency_withdraw_and_stale_terms(self):
        task = self.publish()
        again = self.publish()
        self.assertEqual(again["task_id"], task["task_id"])
        with self.assertRaises(Exception):
            self.b.toy.act("apply", {"task_id": task["task_id"], "terms_hash": "wrong"})
        self.b.toy.act("apply", {"task_id": task["task_id"], "terms_hash": task["terms_hash"]})
        self.b.toy.act("withdraw", {"task_id": task["task_id"]})
        with self.assertRaises(Exception):
            self.a.toy.act("approve", {"task_id": task["task_id"], "node_id": self.b.identity.agent_id})
        with self.assertRaises(Exception):
            self.c.toy.act("approve", {"task_id": task["task_id"], "node_id": self.b.identity.agent_id})

    def test_summary_failures_and_stale_completion(self):
        task = self.publish()
        self.assertEqual(summarize(task, {})["status"], "failed")
        with self.assertRaises(ValueError):
            validate_summary({"status": "ready", "content": {"goal": "missing fields"}})
        with self.assertRaises(ValueError):
            self.a.toy.complete_summary(task["task_id"], "stale", 1, {})
        self.a.toy.run_summary()
        self.a.toy.summary_retry(task["task_id"])
        self.assertEqual(self.a.toy.detail(task["task_id"])["source_hash"], task["source_hash"])

    def test_research_identity_deletion_and_standard_mode(self):
        self.a.toy.trace("test", {"text": "private", "api_key": "never-collect"}, [self.b.identity.agent_id])
        self.a.toy.research.flush()
        self.assertGreater(len(self.research.query()["events"]), 0)
        self.research.delete(self.b.identity.agent_id)
        self.assertFalse(any(self.b.identity.agent_id in e["subjects"] for e in self.research.query()["events"]))
        self.a.toy.trace("test", {"text": "private retry"}, [self.b.identity.agent_id])
        self.a.toy.research.flush()
        self.assertNotIn("private retry", json.dumps(self.research.query()))
        before = self.a.toy.research.status()["counts"]
        self.a.toy.research.config = {"mode": "standard"}
        self.a.toy.trace("private.sent", {"text": "do not collect"})
        self.assertEqual(self.a.toy.research.status()["counts"], before)
        self.assertNotIn("never-collect", json.dumps(redact({"api_key": "never-collect"})))

    def test_no_consent_blocks_task_and_private_send(self):
        self.d.toy.configure_profile("User 3", False)
        with self.assertRaises(ValueError):
            self.d.toy.act("publish", {"title": "no", "original": "no"})
        with self.assertRaises(ValueError):
            self.a.send_direct(self.d.identity.agent_id, "chat.message", {"text": "no"})


class SummaryUnitTests(unittest.TestCase):
    def test_dsh_timeout_and_malformed_output_fail_closed(self):
        task = {"title": "Title", "original": "Task", "acceptance": "Check"}
        config = {"kind": "dsh", "isolated_launcher": ["test-sandbox"], "profile": "test-summary"}
        with patch("agent_sidecar.task_summary.subprocess.run", side_effect=subprocess.TimeoutExpired("dsh", 1)):
            self.assertEqual(summarize(task, config)["status"], "failed")
        with patch("agent_sidecar.task_summary.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"natural language", b"")):
            self.assertEqual(summarize(task, config)["status"], "failed")
        with patch("agent_sidecar.task_summary.subprocess.run") as run:
            self.assertEqual(summarize(task, {"kind": "dsh"})["status"], "failed")
            run.assert_not_called()

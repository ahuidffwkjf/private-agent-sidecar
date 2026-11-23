import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_sidecar.bounties import CHANNEL, SCHEMA, Ledger
from agent_sidecar.broadcast import create_broadcast
from agent_sidecar.crypto import Identity
from agent_sidecar.decisions import DecisionModels, FEATURES
from agent_sidecar.mcp import handle_rpc
from agent_sidecar.relay import make_handler as relay_handler
from agent_sidecar.service import Sidecar, make_handler
from agent_sidecar.store import LocalStore, RelayStore


class BountyFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.relay_store = RelayStore(self.root / "relay.db")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), relay_handler(self.relay_store))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        with patch("agent_sidecar.capabilities.detect_hardware", return_value={}):
            self.nodes = [Sidecar(self.root / str(i), self.url) for i in range(4)]
        self.a, self.b, self.c, self.d = self.nodes
        for node in self.nodes:
            node.decisions.configure(relay=True, node=True)
        self.deadline = int(time.time()) + 3600

    def tearDown(self):
        for node in self.nodes:
            node.bounties.stop()
            node.store.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.relay_store.close()
        self.temp.cleanup()

    def act(self, node, action, **data):
        result = node.bounties.act(action, data, uuid.uuid4().hex)
        self.assertEqual(result["status"], "applied", result)
        return result["event_id"]

    def publish(self, reward=100, depth=3):
        return self.act(self.a, "publish", title="Research task", task="Produce a reviewed result",
                        acceptance="Reference hash and parent approval", reward=reward, unit="credits",
                        deadline=self.deadline, max_depth=depth)

    def offer(self, parent, child, bounty, parent_id="", reward=50, allow_relay=True):
        return self.act(parent, "offer", bounty_id=bounty, parent_id=parent_id,
            child_id=child.identity.agent_id, child_key=child.identity.public().signing_key,
            task="Deliver subtask", reward=reward, acceptance="Parent checks artifact",
            allow_relay=allow_relay)

    def accept(self, parent, child, offer, role="relay", decision="accept"):
        return self.act(child, "respond", offer_id=offer, decision=decision,
            parent_key=parent.identity.public().signing_key, role=role)

    def root_contract(self, reward=100, depth=3):
        bounty = self.publish(reward, depth)
        self.act(self.b, "apply", bounty_id=bounty, proposal="I can organize it")
        offer = self.offer(self.a, self.b, bounty, reward=reward)
        self.accept(self.a, self.b, offer)
        return bounty, offer

    def test_multilevel_tree_and_no_transitive_global_or_project_trust(self):
        bounty, root = self.root_contract()
        child = self.offer(self.b, self.c, bounty, root, 60)
        self.accept(self.b, self.c, child, "node")
        self.act(self.c, "promote", offer_id=child)
        leaf = self.offer(self.c, self.d, bounty, child, 20, False)
        self.accept(self.c, self.d, leaf, "node")
        for node in self.nodes:
            node.bounties.sync()
            snap = node.bounties.snapshot()
            self.assertEqual([o["depth"] for o in snap["offers"]], [1, 2, 3])
            self.assertEqual([o["retained_reward"] for o in snap["offers"]], [40, 40, 20])
            self.assertTrue(all(o["effective_active"] for o in snap["offers"]))
            self.assertEqual(node.store.trusted_ids(), [])
            self.assertEqual(node.store.projects(), [])

    def test_node_activation_and_explicit_consent_required(self):
        bounty, root = self.root_contract()
        child = self.offer(self.b, self.c, bounty, root, 20, False)
        with self.assertRaisesRegex(ValueError, "active authorized"):
            self.offer(self.c, self.d, bounty, child, 10)
        with self.assertRaisesRegex(ValueError, "forbids"):
            self.accept(self.b, self.c, child)
        self.c.decisions.configure(relay=False, node=False)
        with self.assertRaisesRegex(ValueError, "not active"):
            self.accept(self.b, self.c, child, "node")
        self.c.decisions.configure(relay=False, node=True)
        self.accept(self.b, self.c, child, "node")
        self.c.decisions.configure(relay=True, node=True)
        with self.assertRaisesRegex(ValueError, "cannot activate"):
            self.act(self.c, "promote", offer_id=child)

    def test_budget_reserved_before_acceptance_and_reject_releases(self):
        bounty, root = self.root_contract()
        child = self.offer(self.b, self.c, bounty, root, 80)
        with self.assertRaisesRegex(ValueError, "budget"):
            self.offer(self.b, self.d, bounty, root, 30)
        self.accept(self.b, self.c, child, decision="reject")
        self.offer(self.b, self.d, bounty, root, 100)

    def test_revoke_cascades_and_never_releases_accepted_obligations(self):
        bounty, root = self.root_contract()
        child = self.offer(self.b, self.c, bounty, root, 60)
        self.accept(self.b, self.c, child)
        leaf = self.offer(self.c, self.d, bounty, child, 20)
        self.accept(self.c, self.d, leaf)
        self.act(self.a, "revoke", offer_id=root, reason="Stop work")
        self.d.bounties.sync()
        self.assertTrue(all(not o["effective_active"] for o in self.d.bounties.snapshot()["offers"]))
        self.assertEqual(self.a.bounties.snapshot()["bounties"][0]["reserved_reward"], 100)
        with self.assertRaises(ValueError):
            self.act(self.d, "submit", offer_id=leaf, artifact_hash="sha256:" + "a" * 64)

    def test_cycles_depth_and_single_parent_are_rejected(self):
        bounty, root = self.root_contract(depth=2)
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.offer(self.b, self.a, bounty, root, 5)
        child = self.offer(self.b, self.c, bounty, root, 20)
        self.accept(self.b, self.c, child)
        with self.assertRaisesRegex(ValueError, "depth"):
            self.offer(self.c, self.d, bounty, child, 5)
        duplicate = self.offer(self.b, self.c, bounty, root, 10)
        with self.assertRaisesRegex(ValueError, "already has a parent"):
            self.accept(self.b, self.c, duplicate)

    def test_completion_review_is_parent_only_and_no_payment_claim(self):
        bounty, root = self.root_contract()
        child = self.offer(self.b, self.c, bounty, root, 50)
        self.accept(self.b, self.c, child, "node")
        with self.assertRaisesRegex(ValueError, "outstanding"):
            self.act(self.b, "submit", offer_id=root, artifact_hash="sha256:" + "a" * 64)
        submission = self.act(self.c, "submit", offer_id=child, artifact_hash="sha256:" + "a" * 64)
        with self.assertRaisesRegex(ValueError, "party"):
            self.act(self.a, "review", offer_id=child, submission_id=submission, decision="accept", reason="Looks good")
        self.act(self.b, "review", offer_id=child, submission_id=submission, decision="reject", reason="Needs revision")
        second = self.act(self.c, "submit", offer_id=child, artifact_hash="sha256:" + "b" * 64)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.act(self.b, "review", offer_id=child, submission_id=submission, decision="accept", reason="Old version")
        self.act(self.b, "review", offer_id=child, submission_id=second, decision="accept", reason="Verified")
        result = self.act(self.b, "submit", offer_id=root, artifact_hash="sha256:" + "c" * 64)
        self.act(self.a, "review", offer_id=root, submission_id=result, decision="accept", reason="Verified")
        self.assertEqual(self.a.bounties.snapshot()["offers"][0]["payment_status"], "not_implemented")
        self.act(self.a, "close", bounty_id=bounty, reason="Completed")

    def test_expiry_keys_block_and_foreign_ownership(self):
        bounty, root = self.root_contract()
        child = self.offer(self.b, self.c, bounty, root, 20)
        with self.assertRaisesRegex(ValueError, "key"):
            self.act(self.c, "respond", offer_id=child, decision="accept", parent_key="wrong", role="node")
        self.c.store.block(self.b.identity.public())
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.accept(self.b, self.c, child, "node")
        with self.assertRaisesRegex(ValueError, "publisher"):
            self.act(self.d, "close", bounty_id=bounty, reason="Unauthorized")
        with patch("agent_sidecar.bounties.time.time", return_value=self.deadline + 1):
            self.assertEqual(self.a.bounties.snapshot()["bounties"][0]["status"], "expired")
            with self.assertRaisesRegex(ValueError, "expired"):
                self.act(self.b, "apply", bounty_id=bounty, proposal="Late")

    def test_idempotent_retry_after_lost_ack_and_restart(self):
        data = dict(title="Retry", task="Task", acceptance="Criteria", reward=10, unit="credits", deadline=self.deadline, max_depth=2)
        real = self.a.relay.publish_bounty_event

        def lost_ack(event):
            real(event)
            raise OSError("lost ack")

        with patch.object(self.a.relay, "publish_bounty_event", side_effect=lost_ack):
            first = self.a.bounties.act("publish", data, "retry-1")
        self.assertEqual(first["status"], "queued")
        self.a.store.close()
        with patch("agent_sidecar.capabilities.detect_hardware", return_value={}):
            self.a = Sidecar(self.root / "0", self.url)
        self.nodes[0] = self.a
        second = self.a.bounties.act("publish", data, "retry-1")
        self.assertEqual(second, dict(first, status="applied"))
        self.assertEqual(len(self.a.bounties.snapshot()["bounties"]), 1)
        with self.assertRaisesRegex(ValueError, "different content"):
            self.a.bounties.act("publish", dict(data, reward=11), "retry-1")

    def test_concurrent_reservations_do_not_overpromise(self):
        bounty, root = self.root_contract()
        successes, errors = [], []
        def recruit(child):
            try:
                data = dict(bounty_id=bounty, parent_id=root, child_id=child.identity.agent_id,
                    child_key=child.identity.public().signing_key, task="Work", reward=70,
                    acceptance="Review", allow_relay=False)
                successes.append(self.b.bounties.act("offer", data, uuid.uuid4().hex))
            except ValueError as exc:
                errors.append(str(exc))
        threads = [threading.Thread(target=recruit, args=(node,)) for node in (self.c, self.d)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)

    def test_unsent_queue_expires_and_role_disable_prevents_late_action(self):
        bounty = self.publish()
        data = {"bounty_id": bounty, "proposal": "Organize"}
        with patch.object(self.b.relay, "publish_bounty_event", side_effect=OSError("offline")):
            first = self.b.bounties.act("apply", data, "disabled-request")
        self.assertEqual(first["status"], "queued")
        self.b.decisions.configure(relay=False, node=True)
        result = self.b.bounties.act("apply", data, "disabled-request")
        self.assertEqual(result["status"], "rejected")
        self.b.decisions.configure(relay=True, node=True)
        with patch.object(self.b.relay, "publish_bounty_event", side_effect=OSError("offline")):
            second = self.b.bounties.act("apply", data, "expired-request")
        self.assertEqual(second["status"], "queued")
        with patch("agent_sidecar.bounties.time.time", return_value=time.time() + 301):
            result = self.b.bounties.act("apply", data, "expired-request")
        self.assertEqual(result["status"], "expired")
        self.assertEqual(len(self.relay_store.bounty_events()["events"]), 1)

    def test_receiver_rejects_relay_tampering_without_trusting_content(self):
        self.publish()
        with self.relay_store.db:
            row = self.relay_store.db.execute("SELECT seq,event_json FROM bounty_feed").fetchone()
            event = json.loads(row["event_json"])
            event["content"]["data"]["reward"] = 100000
            self.relay_store.db.execute("UPDATE bounty_feed SET event_json=? WHERE seq=?", (json.dumps(event), row["seq"]))
        result = self.d.bounties.sync()
        self.assertEqual(len(result["rejected"]), 1)
        self.assertEqual(self.d.bounties.snapshot()["bounties"], [])
        self.assertEqual(self.d.store.trusted_ids(), [])

    def test_background_poll_is_passive_and_blocked_queued_offer_is_rejected(self):
        bounty, root = self.root_contract()
        self.d.bounties.start()
        deadline = time.monotonic() + 3
        while not self.d.bounties.snapshot()["bounties"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.d.bounties.stop()
        self.assertEqual(len(self.d.bounties.snapshot()["bounties"]), 1)
        self.assertNotIn(self.d.identity.agent_id, self.d.bounties.snapshot()["applications"][bounty])
        data = dict(bounty_id=bounty, parent_id=root, child_id=self.c.identity.agent_id,
                    child_key=self.c.identity.public().signing_key, task="Work", reward=20,
                    acceptance="Review", allow_relay=False)
        with patch.object(self.b.relay, "publish_bounty_event", side_effect=OSError("offline")):
            self.assertEqual(self.b.bounties.act("offer", data, "blocked-queue")["status"], "queued")
        self.b.store.block(self.c.identity.public())
        self.assertEqual(self.b.bounties.act("offer", data, "blocked-queue")["status"], "rejected")

    def test_feed_pagination_signature_validation_and_invalid_transition(self):
        bounty = self.publish()
        # More than a single feed page, all validly signed but unauthorized closes.
        for i in range(105):
            event = create_broadcast(self.d.identity, CHANNEL, {"schema": SCHEMA, "action": "close", "data": {"bounty_id": bounty, "reason": str(i)}}, "application/json", ttl_seconds=self.deadline - int(time.time()))
            self.d.relay.publish_bounty_event(event)
        result = self.c.bounties.sync()
        self.assertEqual(result["received"], 106)
        self.assertEqual(len(result["rejected"]), 105)
        self.assertEqual(self.c.bounties.snapshot()["bounties"][0]["status"], "open")
        tampered = copy.deepcopy(event)
        tampered["content"]["data"]["reason"] = "tampered"
        with self.assertRaises(ValueError):
            self.relay_store.enqueue_bounty_event(tampered)
        page = self.c.relay.bounty_feed(0, 1)
        self.assertTrue(page["has_more"])
        with patch.object(self.c.relay, "bounty_feed", return_value=dict(page, feed_id="different")):
            with self.assertRaisesRegex(ValueError, "identity changed"):
                self.c.bounties.sync()

    def test_http_and_mcp_actions(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.a))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/v1/decisions") as response:
                self.assertTrue(json.load(response)["relay"]["active"])
            body = {"action": "publish", "request_id": "http-1", "data": dict(title="HTTP", task="Work", reward=10, unit="points", acceptance="Review", deadline=self.deadline, max_depth=2)}
            request = Request(base + "/v1/bounties/actions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with urlopen(request) as response:
                self.assertEqual(json.load(response)["status"], "applied")
            with urlopen(base + "/v1/bounties") as response:
                self.assertEqual(len(json.load(response)["bounties"]), 1)
            result = handle_rpc(self.a, {"id": 1, "method": "tools/call", "params": {"name": "decision.roles", "arguments": {"relay": False, "node": True}}})
            self.assertFalse(result["result"]["content"][0]["json"]["relay"]["active"])
            tools = handle_rpc(self.a, {"id": 2, "method": "tools/list"})["result"]["tools"]
            self.assertTrue(any(t["name"] == "bounty.act" for t in tools))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class DecisionModelTests(unittest.TestCase):
    def test_independent_training_activation_persistence_and_no_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.db"
            store = LocalStore(path)
            model = DecisionModels(store)
            self.assertFalse(model.status()["relay"]["active"])
            model.configure(relay=True, node=True)
            before = model.status()["node"]
            for value, target in ((0.0, -0.7), (1.0, 0.8), (0.5, 0.05)):
                model.record("relay", {key: value for key in FEATURES["relay"]}, target)
            result = model.train("relay")
            self.assertLess(result["training_mse_after"], result["training_mse_before"])
            self.assertEqual(model.status()["node"], before)
            for value, target in ((0.0, -0.5), (1.0, 0.9)):
                model.record("node", {key: value for key in FEATURES["node"]}, target)
            model.train("node")
            suggestion = model.suggest("node", [{"id": "job", "features": {key: 0.5 for key in FEATURES["node"]}}])
            self.assertTrue(suggestion["advisory_only"])
            self.assertEqual(store.trusted_ids(), [])
            state = model.status()
            store.close()
            store = LocalStore(path)
            try:
                self.assertEqual(DecisionModels(store).status(), state)
            finally:
                store.close()

    def test_invalid_features_and_inactive_heads_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = LocalStore(Path(temporary) / "state.db")
            try:
                model = DecisionModels(store)
                with self.assertRaises(ValueError):
                    model.suggest("relay", [])
                with self.assertRaises(ValueError):
                    model.configure(relay="true", node=True)
                with self.assertRaises(ValueError):
                    model.train("node")
                with self.assertRaises(ValueError):
                    model.train("node", learning_rate=0.2)
                features = {key: 0.5 for key in FEATURES["node"]}
                for invalid in (float("nan"), float("inf"), True, -1, 2, "1"):
                    with self.assertRaises(ValueError):
                        model.record("node", dict(features, reward=invalid), 0.0)
                with self.assertRaises(ValueError):
                    model.record("node", {"reward": 0.5}, 0)
            finally:
                store.close()

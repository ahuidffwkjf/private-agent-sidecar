import json
import tempfile
import time
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen, Request

from agent_sidecar.preflight import run_preflight
from agent_sidecar.relay import make_handler
from agent_sidecar.service import Sidecar, make_handler as make_sidecar_handler
from agent_sidecar.store import RelayStore



class CapabilityBroadcastTests(unittest.TestCase):
    def test_node_login_broadcasts_hardware_and_network_capabilities(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store)); relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True); relay_thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                alice = Sidecar(root / "alice", relay_url); bob = Sidecar(root / "bob", relay_url)
                self.assertEqual(alice.capabilities["schema"], "sidecar-node-capabilities/1")
                self.assertIn("gpu", alice.capabilities["hardware"])
                self.assertIn("camera", alice.capabilities["hardware"])
                self.assertIn("stability", alice.capabilities["network"])
                nodes = bob.capability_nodes()
                sender_ids = {node["sender_id"] for node in nodes}
                self.assertIn(alice.identity.agent_id, sender_ids)
                self.assertIn(bob.identity.agent_id, sender_ids)
            finally:
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)


class BroadcastTests(unittest.TestCase):
    def test_public_broadcast_visible_without_peer_trust_and_bootstrap_installs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store)); relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True); relay_thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                alice = Sidecar(root / "alice", relay_url); bob = Sidecar(root / "bob", relay_url)
                broadcast = alice.publish_broadcast("project", "# 招募\n需要一个数据分析节点", tags=["recruitment"])
                received = bob.broadcasts()
                self.assertEqual(received[0]["broadcast_id"], broadcast["broadcast_id"])
                first_trace_count = len(bob.store.traces())
                bob.broadcasts()
                self.assertEqual(len(bob.store.traces()), first_trace_count)
                self.assertEqual(received[0]["content_type"], "markdown")
                self.assertTrue((root / "bob" / "mcp" / "manifest.json").exists())
                self.assertTrue((root / "bob" / "trace" / "schema.json").exists())
                self.assertTrue((root / "bob" / "skills" / "manifest.schema.json").exists())
                self.assertIsNotNone(bob.store.bootstrap())
                bob.install_skill({"skill_id": "local.skill", "name": "Local Skill", "version": "0.1.0", "capabilities": ["broadcast.read"]})
                self.assertEqual(bob.store.skills()[0]["skill_id"], "local.skill")
                with self.assertRaises(ValueError):
                    bob.install_skill({"skill_id": "../unsafe", "name": "Bad", "version": "0.1.0"})
                with self.assertRaises(ValueError):
                    bob.install_skill({"skill_id": "bad-exec", "name": "Bad", "version": "0.1.0", "execution": "shell"})
                self.assertTrue((root / "bob" / "skills" / "local.skill.json").exists())
                ui_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(bob))
                ui_thread = threading.Thread(target=ui_server.serve_forever, daemon=True); ui_thread.start()
                try:
                    with urlopen(f"http://127.0.0.1:{ui_server.server_port}/v1/mcp/manifest") as response:
                        self.assertTrue(any(tool["name"] == "broadcast.publish" for tool in json.loads(response.read())["tools"]))
                    request = Request(f"http://127.0.0.1:{ui_server.server_port}/mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
                    with urlopen(request) as response:
                        tools = json.loads(response.read())["result"]["tools"]
                        self.assertTrue(any(tool["name"] == "skill.install" for tool in tools))
                        # Project trust is exposed to Harnesses through the same MCP surface.
                        self.assertTrue(any(tool["name"] == "project.close" for tool in tools))
                finally:
                    ui_server.shutdown(); ui_server.server_close(); ui_thread.join(timeout=2)
            finally:
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)


class TrustUITests(unittest.TestCase):
    def test_untrusted_peer_cannot_send(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            thread = threading.Thread(target=relay_server.serve_forever, daemon=True)
            thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                alice = Sidecar(root / "alice", relay_url)
                bob = Sidecar(root / "bob", relay_url)
                with self.assertRaises(ValueError):
                    alice.send(bob.identity.agent_id, "fl.update.submit", {"round_id": "r1"})
            finally:
                relay_server.shutdown(); relay_server.server_close(); thread.join(timeout=2)

    def test_new_node_candidates_are_pending_and_can_be_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True)
            relay_thread.start()
            relay_url = f"http://127.0.0.1:{relay_server.server_port}"
            alice = Sidecar(root / "alice", relay_url)
            bob = Sidecar(root / "bob", relay_url)
            sidecar_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(alice))
            sidecar_thread = threading.Thread(target=sidecar_server.serve_forever, daemon=True)
            sidecar_thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{sidecar_server.server_port}/v1/peers/candidates") as response:
                    candidates = json.loads(response.read().decode("utf-8"))["peers"]
                self.assertEqual(candidates[0]["trust_state"], "pending")
                request = Request(f"http://127.0.0.1:{sidecar_server.server_port}/v1/peers/trust", data=json.dumps({"trusted_agent_ids": [bob.identity.agent_id], "expected_signing_keys": {bob.identity.agent_id: bob.identity.public().signing_key}}).encode(), headers={"Content-Type": "application/json"}, method="PUT")
                with urlopen(request) as response:
                    body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(body["trusted_agent_ids"], [bob.identity.agent_id])
                self.assertIn(bob.identity.agent_id, alice.store.trusted_ids())
                with urlopen(f"http://127.0.0.1:{sidecar_server.server_port}/") as response:
                    self.assertIn("Sidecar · 聊天", response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{sidecar_server.server_port}/ui/admin") as response:
                    dashboard = response.read().decode("utf-8")
                self.assertIn("节点级长期信任", dashboard)
                self.assertIn("所有 active 项目中可用", dashboard)
                self.assertIn("/v1/peers/trust-requests", dashboard)
                self.assertIn("refresh_global_trust", dashboard)
                self.assertIn("/v1/peers/trust-notifications", dashboard)
                self.assertIn("expected_signing_key", dashboard)
                self.assertIn("节点级长期信任", dashboard)
                self.assertIn("/v1/peers/trust", dashboard)
                self.assertIn("private_message_peer", dashboard)
                self.assertIn("send_private_message", dashboard)
                self.assertIn("target_kind", dashboard)
                self.assertIn("project_auto_trust", dashboard)
                self.assertIn("bindDashboard", dashboard)
                self.assertIn("dataset.dashboardReady", dashboard)
                self.assertIn("JSON.stringify(x.content,null,2)", dashboard)
                self.assertIn("/v1/messages/send", dashboard)
                with urlopen(f"http://127.0.0.1:{sidecar_server.server_port}/ui/trust") as response:
                    self.assertIn("信任管理".encode("utf-8"), response.read())
            finally:
                sidecar_server.shutdown(); sidecar_server.server_close(); sidecar_thread.join(timeout=2)
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)


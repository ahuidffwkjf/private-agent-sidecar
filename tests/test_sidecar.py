import copy
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from agent_sidecar.crypto import Identity
from agent_sidecar.client import RelayClient
from agent_sidecar.protocol import decrypt_message, encrypt_message
from agent_sidecar.relay import make_handler
from agent_sidecar.service import Sidecar
from agent_sidecar.store import RelayStore
from agent_sidecar.http_utils import enable_tls


class HttpSecurityTests(unittest.TestCase):
    def test_tls_requires_cert_and_key_together(self):
        with self.assertRaises(ValueError):
            enable_tls(object(), Path("cert.pem"), None)


class CryptoTests(unittest.TestCase):
    def test_round_trip_and_tamper_detection(self):
        alice = Identity.generate()
        bob = Identity.generate()
        envelope = encrypt_message(alice, bob.public(), 1, "task.request", {"text": "秘密"})
        self.assertEqual(
            decrypt_message(bob, envelope, alice.public()),
            {"type": "task.request", "payload": {"text": "秘密"}},
        )

        tampered = copy.deepcopy(envelope)
        tampered["ciphertext"] = tampered["ciphertext"][:-1] + ("A" if tampered["ciphertext"][-1] != "A" else "B")
        with self.assertRaises(ValueError):
            decrypt_message(bob, tampered, alice.public())


class RelayAuthTests(unittest.TestCase):
    def test_optional_relay_access_token_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            relay_store = RelayStore(Path(temporary) / "relay.db")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store, "secret-token"))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                relay_url = f"http://127.0.0.1:{server.server_port}"
                identity = Identity.generate()
                with self.assertRaises(RuntimeError):
                    RelayClient(relay_url, identity, "wrong-token").register()
                RelayClient(relay_url, identity, "secret-token").register()
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


class RelayRateLimitTests(unittest.TestCase):
    def test_relay_rate_limit_returns_429(self):
        with tempfile.TemporaryDirectory() as temporary:
            relay_store = RelayStore(Path(temporary) / "relay.db")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store, rate_limit_per_minute=2))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                urlopen(base + "/v1/agents")
                urlopen(base + "/v1/agents")
                with self.assertRaises(Exception) as context:
                    urlopen(base + "/v1/agents")
                self.assertIn("429", str(context.exception))
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


class ProjectScopedTrustTests(unittest.TestCase):
    def test_project_trust_expires_when_project_closes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                relay_url = f"http://127.0.0.1:{server.server_port}"
                alice = Sidecar(root / "alice", relay_url); bob = Sidecar(root / "bob", relay_url)
                project_id = "project-temporary-trust"
                alice.create_project("临时项目", project_id); bob.create_project("临时项目", project_id, owner_id=alice.identity.agent_id)
                alice.trust_for_project(project_id, bob.identity.agent_id, bob.identity.public().signing_key)
                bob.trust_for_project(project_id, alice.identity.agent_id, alice.identity.public().signing_key)
                self.assertNotIn(bob.identity.agent_id, alice.store.trusted_ids())
                message_id = alice.send(bob.identity.agent_id, "task.request", {"text": "project-only"}, project_id=project_id)
                received = bob.receive()
                self.assertEqual(received[0]["message_id"], message_id)
                self.assertEqual(received[0]["project_id"], project_id)
                alice.close_project(project_id)
                close_events = bob.receive()
                self.assertEqual(close_events[0]["type"], "project.lifecycle.close")
                self.assertEqual(bob.store.project(project_id)["status"], "closed")
                self.assertIsNone(alice.store.project_peer(project_id, bob.identity.agent_id))
                with self.assertRaises(ValueError):
                    alice.send(bob.identity.agent_id, "task.request", {"text": "after-close"}, project_id=project_id)
                self.assertEqual(alice.store.project(project_id)["status"], "closed")
                self.assertIsNotNone(alice.store.project(project_id)["members"][0]["revoked_at"])
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_global_trust_survives_project_close(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                relay_url = f"http://127.0.0.1:{server.server_port}"
                alice = Sidecar(root / "alice", relay_url); bob = Sidecar(root / "bob", relay_url)
                alice.trust(bob.identity.agent_id, bob.identity.public().signing_key); bob.trust(alice.identity.agent_id, alice.identity.public().signing_key)
                alice.create_project("项目", "project-global"); alice.trust_for_project("project-global", bob.identity.agent_id)
                alice.close_project("project-global")
                message_id = alice.send(bob.identity.agent_id, "message", {"text": "global-still-valid"})
                received = bob.receive()
                self.assertIn(message_id, [item["message_id"] for item in received])
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


class NetworkTests(unittest.TestCase):
    def test_two_sidecars_exchange_and_acknowledge(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            relay_url = "http://127.0.0.1:%d" % server.server_port
            try:
                alice = Sidecar(root / "alice", relay_url)
                bob = Sidecar(root / "bob", relay_url)
                alice.trust(bob.identity.agent_id, bob.identity.public().signing_key)
                bob.trust(alice.identity.agent_id, alice.identity.public().signing_key)

                message_id = alice.send(bob.identity.agent_id, "task.request", {"text": "hello"})
                received = bob.receive()
                self.assertEqual(len(received), 1)
                self.assertEqual(received[0]["message_id"], message_id)
                self.assertEqual(received[0]["payload"], {"text": "hello"})
                self.assertEqual(bob.receive(), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()


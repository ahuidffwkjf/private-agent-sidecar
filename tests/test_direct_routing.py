import json
import datetime
import sqlite3
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agent_sidecar.relay import make_handler as make_relay_handler
from agent_sidecar.http_utils import enable_tls
from agent_sidecar.service import Sidecar, certificate_fingerprint, make_handler as make_sidecar_handler
from agent_sidecar.store import RelayStore


class DirectRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_relay_handler(RelayStore(self.root / "relay.db")))
        self.relay_thread = threading.Thread(target=self.relay_server.serve_forever, daemon=True)
        self.relay_thread.start()
        self.relay_url = f"http://127.0.0.1:{self.relay_server.server_port}"
        self.alice = Sidecar(self.root / "alice", self.relay_url)
        self.bob = Sidecar(self.root / "bob", self.relay_url)
        self.alice_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(self.alice))
        self.bob_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(self.bob))
        self.alice_thread = threading.Thread(target=self.alice_server.serve_forever, daemon=True)
        self.bob_thread = threading.Thread(target=self.bob_server.serve_forever, daemon=True)
        self.alice_thread.start(); self.bob_thread.start()
        self.alice.set_direct_endpoint(f"http://127.0.0.1:{self.alice_server.server_port}")
        self.bob.set_direct_endpoint(f"http://127.0.0.1:{self.bob_server.server_port}")

    def tearDown(self):
        for server, thread in ((self.alice_server, self.alice_thread), (self.bob_server, self.bob_thread), (self.relay_server, self.relay_thread)):
            server.shutdown(); server.server_close(); thread.join(timeout=2)
        self.temporary.cleanup()

    def relay_message_count(self):
        db = sqlite3.connect(self.root / "relay.db")
        try:
            return db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            db.close()

    def test_private_direct_message_never_enters_relay(self):
        self.alice.trust(self.bob.identity.agent_id, self.bob.identity.public().signing_key)
        self.bob.trust(self.alice.identity.agent_id, self.alice.identity.public().signing_key)
        before = self.relay_message_count()
        result = self.alice.send_routed({"kind": "private", "recipient_id": self.bob.identity.agent_id}, {"type": "chat.message", "payload": {"text": "direct"}})
        self.assertEqual(result["transport"], "direct")
        self.assertEqual(self.relay_message_count(), before)
        self.assertTrue(self.bob.store.has_received(result["deliveries"][0]["message_id"]))

    def test_https_direct_transport_uses_certificate_fingerprint_pinning(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1))
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256())
        )
        cert_path = self.root / "direct-cert.pem"
        key_path = self.root / "direct-key.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        tls_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(self.bob))
        enable_tls(tls_server, cert_path, key_path)
        tls_thread = threading.Thread(target=tls_server.serve_forever, daemon=True)
        tls_thread.start()
        endpoint = f"https://127.0.0.1:{tls_server.server_port}"
        try:
            fingerprint = certificate_fingerprint(cert_path)
            self.bob.set_direct_endpoint(endpoint, fingerprint)
            self.alice.trust(self.bob.identity.agent_id, self.bob.identity.public().signing_key)
            self.bob.trust(self.alice.identity.agent_id, self.alice.identity.public().signing_key)
            result = self.alice.send_direct(self.bob.identity.agent_id, "chat.message", {"text": "tls"})
            self.assertEqual(result["status"], "delivered")
            self.assertTrue(self.bob.store.has_received(result["message_id"]))
        finally:
            tls_server.shutdown(); tls_server.server_close(); tls_thread.join(timeout=2)

    def test_project_direct_fanout_and_auto_trust_use_signed_manifest(self):
        project_id = "project-direct"
        self.alice.create_project("Direct", project_id)
        self.bob.create_project("Direct", project_id, owner_id=self.alice.identity.agent_id)
        self.alice.trust_for_project(project_id, self.bob.identity.agent_id)
        self.bob.enable_project_auto_trust(project_id, True)
        self.assertIsNotNone(self.bob.store.project_peer(project_id, self.alice.identity.agent_id))
        before = self.relay_message_count()
        result = self.alice.send_routed({"kind": "project", "project_id": project_id}, {"type": "chat.message", "payload": {"text": "group"}})
        self.assertEqual(result["transport"], "direct")
        self.assertEqual(result["deliveries"][0]["status"], "delivered")
        self.assertEqual(self.relay_message_count(), before)
        self.assertTrue(self.bob.store.has_received(result["deliveries"][0]["message_id"]))

    def test_project_board_http_api_commits_and_lists_events(self):
        project_id = "project-board-api"
        self.alice.create_project("Board API", project_id)
        self.bob.create_project("Board API", project_id, owner_id=self.alice.identity.agent_id)
        self.alice.trust_for_project(project_id, self.bob.identity.agent_id)
        base = f"http://127.0.0.1:{self.alice_server.server_port}"
        request = Request(
            f"{base}/v1/projects/{project_id}/board",
            data=json.dumps({"event_type": "post", "content": {"text": "api post"}}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            created = json.loads(response.read())
        with urlopen(f"{base}/v1/projects/{project_id}/board") as response:
            board = json.loads(response.read())
        self.assertEqual(board["event_count"], 1)
        self.assertEqual(board["events"][0]["event_id"], created["event"]["event_id"])
        self.assertEqual(board["events"][0]["content"]["text"], "api post")

    def test_merkle_range_exchange_repairs_event_created_while_peer_was_offline(self):
        project_id = "project-merkle-sync"
        self.alice.create_project("Merkle Sync", project_id)
        self.bob.create_project("Merkle Sync", project_id, owner_id=self.alice.identity.agent_id)
        self.alice.trust_for_project(project_id, self.bob.identity.agent_id)

        self.alice.set_direct_endpoint("")
        self.bob.store.upsert_project_member(project_id, self.alice.identity.public(), "", "owner")
        delayed = self.bob.post_project_board_event(project_id, "post", {"text": "created offline"})["event"]
        self.assertNotIn(delayed["event_id"], {event["event_id"] for event in self.alice.store.project_board_events(project_id)})

        self.alice.set_direct_endpoint(f"http://127.0.0.1:{self.alice_server.server_port}")
        result = self.alice.sync_project_board_merkle(project_id)
        self.assertEqual(result["transport"], "direct-merkle-range")
        self.assertIn(delayed["event_id"], {event["event_id"] for event in self.alice.store.project_board_events(project_id)})
        self.assertEqual(result["deliveries"][0]["status"], "delivered")

    def test_project_gossip_fanout_uses_partial_member_neighbors(self):
        carol = Sidecar(self.root / "carol-gossip", self.relay_url)
        carol_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(carol))
        carol_thread = threading.Thread(target=carol_server.serve_forever, daemon=True)
        carol_thread.start()
        carol.set_direct_endpoint(f"http://127.0.0.1:{carol_server.server_port}")
        project_id = "project-gossip-fanout"
        try:
            self.alice.create_project("Gossip Fanout", project_id)
            self.bob.create_project("Gossip Fanout", project_id, owner_id=self.alice.identity.agent_id)
            carol.create_project("Gossip Fanout", project_id, owner_id=self.alice.identity.agent_id)
            self.alice.trust_for_project(project_id, self.bob.identity.agent_id)
            self.alice.trust_for_project(project_id, carol.identity.agent_id)
            for node in (self.alice, self.bob, carol):
                node.set_project_gossip_fanout(project_id, 1)
            for node in (self.alice, self.bob, carol):
                peers = node.project_gossip_peers(project_id)
                self.assertEqual(len(peers), 1)
                self.assertNotEqual(peers[0]["agent_id"], node.identity.agent_id)
            scores = self.alice.project_gossip_peer_scores(project_id)
            self.assertEqual(len(scores), 2)
            self.assertTrue(all("recent_successes" in score and "recent_failures" in score for score in scores))
        finally:
            carol_server.shutdown(); carol_server.server_close(); carol_thread.join(timeout=2)

    def test_gossip_peer_probe_reports_live_direct_endpoint_latency(self):
        project_id = "project-gossip-probe"
        self.alice.create_project("Gossip Probe", project_id)
        self.bob.create_project("Gossip Probe", project_id, owner_id=self.alice.identity.agent_id)
        self.alice.trust_for_project(project_id, self.bob.identity.agent_id)
        scores = self.alice.project_gossip_peer_scores(project_id, probe=True)
        peer = next(score for score in scores if score["agent_id"] == self.bob.identity.agent_id)
        self.assertTrue(peer["probe_online"])
        self.assertIsNotNone(peer["probe_latency_ms"])
        self.assertIsNone(peer["probe_error"])

    def test_gossip_neighbor_selection_prefers_peer_with_recent_success_after_failure(self):
        carol = Sidecar(self.root / "carol-health", self.relay_url)
        carol_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(carol))
        carol_thread = threading.Thread(target=carol_server.serve_forever, daemon=True)
        carol_thread.start()
        carol.set_direct_endpoint(f"http://127.0.0.1:{carol_server.server_port}")
        project_id = "project-gossip-health"
        try:
            self.alice.create_project("Gossip Health", project_id)
            self.bob.create_project("Gossip Health", project_id, owner_id=self.alice.identity.agent_id)
            carol.create_project("Gossip Health", project_id, owner_id=self.alice.identity.agent_id)
            self.alice.trust_for_project(project_id, self.bob.identity.agent_id)
            self.alice.trust_for_project(project_id, carol.identity.agent_id)
            self.alice.set_project_gossip_fanout(project_id, 1)
            self.alice.store.record_project_gossip_result(project_id, self.bob.identity.agent_id, False)
            for _ in range(3):
                self.alice.store.record_project_gossip_result(project_id, carol.identity.agent_id, True)
            selected = self.alice.project_gossip_peers(project_id)
            self.assertEqual(selected[0]["agent_id"], carol.identity.agent_id)
        finally:
            carol_server.shutdown(); carol_server.server_close(); carol_thread.join(timeout=2)

    def test_gossip_peer_score_marks_recent_failure_cooldown(self):
        project_id = "project-gossip-cooldown"
        self.alice.create_project("Gossip Cooldown", project_id)
        self.bob.create_project("Gossip Cooldown", project_id, owner_id=self.alice.identity.agent_id)
        self.alice.trust_for_project(project_id, self.bob.identity.agent_id)
        self.alice.store.record_project_gossip_result(project_id, self.bob.identity.agent_id, False)
        score = next(item for item in self.alice.project_gossip_peer_scores(project_id) if item["agent_id"] == self.bob.identity.agent_id)
        self.assertTrue(score["failure_cooldown_active"])
        self.assertEqual(score["failure_cooldown_seconds"], 30)

    def test_project_invitation_requires_explicit_acceptance_before_membership(self):
        project_id = "project-invitation"
        self.alice.create_project("Invitation", project_id)
        sent = self.alice.send_project_invitation(project_id, self.bob.identity.agent_id)
        invitation_id = sent["invitation"]["invitation_id"]
        self.assertEqual(sent["invitation"]["invitation_kind"], "project-temporary-trust")
        self.assertIsNone(self.alice.store.project_member(project_id, self.bob.identity.agent_id))
        self.assertIsNone(self.bob.store.project(project_id))

        feed = self.bob.project_invitation_notifications()
        self.assertEqual(feed["incoming"][0]["invitation_id"], invitation_id)
        self.assertEqual(feed["incoming"][0]["status"], "pending")
        self.assertIsNone(self.bob.store.project(project_id))

        accepted = self.bob.respond_project_invitation(invitation_id, "accept")
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(self.bob.store.project(project_id)["owner_id"], self.alice.identity.agent_id)
        self.assertIsNone(self.alice.store.project_member(project_id, self.bob.identity.agent_id))

        owner_feed = self.alice.project_invitation_notifications()
        self.assertEqual(owner_feed["outgoing"][0]["status"], "completed")
        self.assertIsNotNone(self.alice.store.project_member(project_id, self.bob.identity.agent_id))
        self.assertIsNotNone(self.bob.store.project_member(project_id, self.bob.identity.agent_id))
        self.assertIsNotNone(self.bob.store.project_peer(project_id, self.alice.identity.agent_id))
        self.assertNotIn(self.bob.identity.agent_id, self.alice.store.trusted_ids())
        self.assertNotIn(self.alice.identity.agent_id, self.bob.store.trusted_ids())

    def test_rejected_project_invitation_never_adds_member(self):
        project_id = "project-invitation-reject"
        self.alice.create_project("Invitation Reject", project_id)
        invitation_id = self.alice.send_project_invitation(project_id, self.bob.identity.agent_id)["invitation"]["invitation_id"]
        self.bob.project_invitation_notifications()
        rejected = self.bob.respond_project_invitation(invitation_id, "reject")
        self.assertEqual(rejected["status"], "rejected")
        self.alice.project_invitation_notifications()
        self.assertIsNone(self.alice.store.project_member(project_id, self.bob.identity.agent_id))
        self.assertIsNone(self.bob.store.project(project_id))

    def test_mutually_trusted_peer_invitation_is_labeled_without_changing_scope(self):
        self.alice.request_peer_trust(self.bob.identity.agent_id)
        self.bob.peer_trust_notifications()
        self.bob.respond_peer_trust(self.alice.identity.agent_id, "accept")
        self.alice.peer_trust_notifications()
        project_id = "project-trusted-invitation"
        self.alice.create_project("Trusted Invitation", project_id)
        sent = self.alice.send_project_invitation(project_id, self.bob.identity.agent_id)
        self.assertEqual(sent["invitation"]["invitation_kind"], "mutual-node-trust")
        self.assertIsNone(self.alice.store.project_member(project_id, self.bob.identity.agent_id))

    def test_project_invitation_http_api_accepts_before_owner_adds_member(self):
        project_id = "project-invitation-api"
        self.alice.create_project("Invitation API", project_id)
        alice_url = f"http://127.0.0.1:{self.alice_server.server_port}"
        bob_url = f"http://127.0.0.1:{self.bob_server.server_port}"
        request = Request(
            f"{alice_url}/v1/projects/{project_id}/invitations",
            data=json.dumps({"agent_id": self.bob.identity.agent_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            invitation_id = json.loads(response.read())["invitation"]["invitation_id"]
        with urlopen(bob_url + "/v1/project-invitations") as response:
            incoming = json.loads(response.read())["incoming"]
        self.assertEqual(incoming[0]["invitation_id"], invitation_id)
        accept = Request(
            f"{bob_url}/v1/project-invitations/{invitation_id}/response",
            data=json.dumps({"decision": "accept"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(accept) as response:
            self.assertEqual(json.loads(response.read())["status"], "accepted")
        self.assertIsNone(self.alice.store.project_member(project_id, self.bob.identity.agent_id))
        with urlopen(alice_url + "/v1/project-invitations") as response:
            outgoing = json.loads(response.read())["outgoing"]
        self.assertEqual(outgoing[0]["status"], "completed")
        self.assertIsNotNone(self.alice.store.project_member(project_id, self.bob.identity.agent_id))

    def test_accepted_invitation_retries_membership_delivery_when_invitee_returns(self):
        project_id = "project-invitation-retry"
        self.alice.create_project("Invitation Retry", project_id)
        invitation_id = self.alice.send_project_invitation(project_id, self.bob.identity.agent_id)["invitation"]["invitation_id"]
        self.bob.project_invitation_notifications()
        self.bob.respond_project_invitation(invitation_id, "accept")

        self.bob.set_direct_endpoint("")
        first = self.alice.project_invitation_notifications()
        self.assertEqual(first["outgoing"][0]["status"], "accepted")
        self.assertIsNotNone(self.alice.store.project_member(project_id, self.bob.identity.agent_id))
        self.assertIsNone(self.bob.store.project_member(project_id, self.bob.identity.agent_id))

        self.bob.set_direct_endpoint(f"http://127.0.0.1:{self.bob_server.server_port}")
        second = self.alice.project_invitation_notifications()
        self.assertEqual(second["outgoing"][0]["status"], "completed")
        self.assertIsNotNone(self.bob.store.project_member(project_id, self.bob.identity.agent_id))

    def test_historical_manifest_proof_allows_delayed_old_epoch_board_event(self):
        carol = Sidecar(self.root / "carol-history", self.relay_url)
        carol_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(carol))
        carol_thread = threading.Thread(target=carol_server.serve_forever, daemon=True)
        carol_thread.start()
        carol.set_direct_endpoint(f"http://127.0.0.1:{carol_server.server_port}")
        project_id = "project-history"
        try:
            self.alice.create_project("History", project_id)
            self.bob.create_project("History", project_id, owner_id=self.alice.identity.agent_id)
            carol.create_project("History", project_id, owner_id=self.alice.identity.agent_id)
            self.alice.trust_for_project(project_id, self.bob.identity.agent_id)
            old_event = self.alice.post_project_board_event(project_id, "post", {"text": "epoch two plan"})["event"]
            old_epoch = old_event["membership_epoch"]

            self.alice.trust_for_project(project_id, carol.identity.agent_id)
            self.assertIsNotNone(carol.store.project_manifest_at(project_id, old_epoch))
            # Carol joined after the old epoch and therefore receives the
            # signed historical manifest metadata but not its content key.
            # Historical content is intentionally not readable by default.
            with self.assertRaises(ValueError):
                carol.apply_project_board_events(project_id, [old_event])

            tampered = dict(old_event)
            tampered["membership_proof"] = dict(old_event["membership_proof"])
            tampered["membership_proof"]["signature"] = "tampered"
            with self.assertRaises(ValueError):
                carol.apply_project_board_events(project_id, [tampered])
        finally:
            carol_server.shutdown(); carol_server.server_close(); carol_thread.join(timeout=2)

    def test_project_content_key_rotates_and_removed_member_cannot_read_new_epoch(self):
        carol = Sidecar(self.root / "carol-key", self.relay_url)
        carol_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(carol))
        carol_thread = threading.Thread(target=carol_server.serve_forever, daemon=True)
        carol_thread.start()
        carol.set_direct_endpoint(f"http://127.0.0.1:{carol_server.server_port}")
        project_id = "project-key-rotation"
        try:
            self.alice.create_project("Key Rotation", project_id)
            self.bob.create_project("Key Rotation", project_id, owner_id=self.alice.identity.agent_id)
            carol.create_project("Key Rotation", project_id, owner_id=self.alice.identity.agent_id)
            self.alice.trust_for_project(project_id, self.bob.identity.agent_id)
            self.alice.trust_for_project(project_id, carol.identity.agent_id)
            old_key = carol.store.project_content_key(project_id, 3)
            self.assertIsNotNone(old_key)

            self.alice.store.revoke_project_member(project_id, carol.identity.agent_id)
            removal = self.alice._build_project_manifest(project_id)
            self.bob.apply_project_manifest(removal)
            carol.apply_project_manifest(removal)
            new_key = self.bob.store.project_content_key(project_id, 4)
            self.assertIsNotNone(new_key)
            self.assertNotEqual(old_key["key_fingerprint"], new_key["key_fingerprint"])
            self.assertIsNone(carol.store.project_content_key(project_id, 4))

            new_event = self.alice.post_project_board_event(project_id, "post", {"text": "epoch four secret"})["event"]
            self.assertIsInstance(new_event["content"], dict)
            self.assertEqual(new_event["content"].get("protocol"), "project-content-key/1")
            with self.assertRaises(ValueError):
                carol.apply_project_board_events(project_id, [new_event])
        finally:
            carol_server.shutdown(); carol_server.server_close(); carol_thread.join(timeout=2)

    def test_distributed_project_board_remains_usable_while_owner_is_offline(self):
        carol = Sidecar(self.root / "carol", self.relay_url)
        carol_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(carol))
        carol_thread = threading.Thread(target=carol_server.serve_forever, daemon=True)
        carol_thread.start()
        carol.set_direct_endpoint(f"http://127.0.0.1:{carol_server.server_port}")
        project_id = "project-board"
        try:
            self.alice.create_project("Board", project_id)
            self.bob.create_project("Board", project_id, owner_id=self.alice.identity.agent_id)
            carol.create_project("Board", project_id, owner_id=self.alice.identity.agent_id)

            self.alice.trust_for_project(project_id, self.bob.identity.agent_id)
            self.alice.trust_for_project(project_id, carol.identity.agent_id)

            self.assertTrue(self.bob.store.project_auto_trust(project_id))
            self.assertIsNotNone(self.bob.store.project_peer(project_id, carol.identity.agent_id))
            before = self.relay_message_count()

            first = self.alice.post_project_board_event(project_id, "post", {"text": "project plan"})
            first_id = first["event"]["event_id"]
            self.assertIn(first_id, {event["event_id"] for event in self.bob.store.project_board_events(project_id)})
            self.assertIn(first_id, {event["event_id"] for event in carol.store.project_board_events(project_id)})
            incremental = self.alice.sync_project_board(project_id)
            self.assertEqual(incremental["event_count"], 0)
            self.assertEqual(incremental["available_event_count"], 1)

            self.alice_server.shutdown()
            self.alice_server.server_close()
            self.alice_thread.join(timeout=2)

            second = self.bob.post_project_board_event(project_id, "reply", {"text": "owner can read this later"}, first_id)
            second_id = second["event"]["event_id"]
            deliveries = {item["recipient_id"]: item["status"] for item in second["sync"]["deliveries"]}
            self.assertEqual(deliveries[carol.identity.agent_id], "accepted")
            self.assertEqual(deliveries[self.alice.identity.agent_id], "failed")
            self.assertIn(second_id, {event["event_id"] for event in carol.store.project_board_events(project_id)})
            self.assertEqual(self.relay_message_count(), before)

            tampered = dict(second["event"])
            tampered["content"] = {"text": "tampered"}
            with self.assertRaises(ValueError):
                carol.apply_project_board_events(project_id, [tampered])
        finally:
            carol_server.shutdown(); carol_server.server_close(); carol_thread.join(timeout=2)
            # The owner server is normally closed by tearDown. Replace it with
            # an inert server object only when this test already stopped it.
            if not self.alice_thread.is_alive():
                self.alice_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(self.alice))
                self.alice_thread = threading.Thread(target=self.alice_server.serve_forever, daemon=True)
                self.alice_thread.start()

    def test_newer_owner_manifest_revokes_removed_member(self):
        project_id = "project-removal"
        self.alice.create_project("Removal", project_id)
        self.bob.create_project("Removal", project_id, owner_id=self.alice.identity.agent_id)
        self.alice.trust_for_project(project_id, self.bob.identity.agent_id)
        self.bob.enable_project_auto_trust(project_id, True)
        self.assertIsNotNone(self.bob.store.project_member(project_id, self.bob.identity.agent_id))

        self.alice.store.revoke_project_member(project_id, self.bob.identity.agent_id)
        removal_manifest = self.alice._build_project_manifest(project_id)
        result = self.bob.apply_project_manifest(removal_manifest)

        self.assertEqual(result["status"], "accepted")
        self.assertIsNone(self.bob.store.project_member(project_id, self.bob.identity.agent_id))
        with self.assertRaises(ValueError):
            self.bob.post_project_board_event(project_id, "post", {"text": "revoked member"})

    def test_non_owner_membership_manifest_is_rejected(self):
        project_id = "project-manifest"
        self.alice.create_project("Manifest", project_id)
        self.bob.create_project("Manifest", project_id, owner_id=self.alice.identity.agent_id)
        self.alice.trust_for_project(project_id, self.bob.identity.agent_id)
        self.bob.trust_for_project(project_id, self.alice.identity.agent_id)
        forged = {"manifest": {"project_id": project_id, "owner_id": self.alice.identity.agent_id, "epoch": 99, "members": []}, "signature": self.bob.identity.sign(b"forged")}
        with self.assertRaises(RuntimeError):
            self.bob._send_direct_to_peer(self.alice.identity.public(), self.alice.direct_endpoint, "project.membership.manifest", forged, project_id=project_id, scope="membership", trace_prefix="project.membership")

    def test_global_trusted_peer_is_project_candidate_without_project_grant(self):
        self.alice.trust(self.bob.identity.agent_id, self.bob.identity.public().signing_key)
        project_id = "project-candidate"
        self.alice.create_project("Candidate", project_id)
        candidates = {item["agent_id"]: item for item in self.alice.peer_candidates(project_id)}
        self.assertNotIn(self.alice.identity.agent_id, candidates)
        self.assertEqual(candidates[self.bob.identity.agent_id]["source"], "global-trusted")
        self.assertFalse(candidates[self.bob.identity.agent_id]["project_trusted"])

    def test_direct_failure_is_fail_closed_without_relay_fallback(self):
        self.alice.trust(self.bob.identity.agent_id, self.bob.identity.public().signing_key)
        self.bob.set_direct_endpoint("")
        before = self.relay_message_count()
        with self.assertRaises(ValueError):
            self.alice.send_direct(self.bob.identity.agent_id, "chat.message", {"text": "blocked"})
        self.assertEqual(self.relay_message_count(), before)


if __name__ == "__main__":
    unittest.main()

class PrivateChatMutualTrustTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_relay_handler(RelayStore(self.root / "relay.db")))
        self.relay_thread = threading.Thread(target=self.relay_server.serve_forever, daemon=True); self.relay_thread.start()
        relay_url = f"http://127.0.0.1:{self.relay_server.server_port}"
        self.alice = Sidecar(self.root / "alice", relay_url)
        self.bob = Sidecar(self.root / "bob", relay_url)
        self.alice_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(self.alice))
        self.bob_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(self.bob))
        self.alice_thread = threading.Thread(target=self.alice_server.serve_forever, daemon=True); self.alice_thread.start()
        self.bob_thread = threading.Thread(target=self.bob_server.serve_forever, daemon=True); self.bob_thread.start()
        self.alice.set_direct_endpoint(f"http://127.0.0.1:{self.alice_server.server_port}")
        self.bob.set_direct_endpoint(f"http://127.0.0.1:{self.bob_server.server_port}")

    def tearDown(self):
        for server, thread in ((self.alice_server, self.alice_thread), (self.bob_server, self.bob_thread), (self.relay_server, self.relay_thread)):
            server.shutdown(); server.server_close(); thread.join(timeout=2)
        self.temporary.cleanup()

    def relay_message_count(self):
        db = sqlite3.connect(self.root / "relay.db")
        try:
            return db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            db.close()

    def test_private_chat_requires_mutual_trust_and_uses_direct_transport(self):
        self.alice.request_peer_trust(self.bob.identity.agent_id, self.bob.identity.public().signing_key)
        self.bob.peer_trust_notifications()
        with self.assertRaisesRegex(ValueError, "mutual trust"):
            self.alice.send_direct(self.bob.identity.agent_id, "chat.message", {"text": "blocked"})
        self.bob.respond_peer_trust(self.alice.identity.agent_id, "accept")
        self.alice.peer_trust_notifications()
        before = self.relay_message_count()
        sent = self.alice.send_direct(self.bob.identity.agent_id, "chat.message", {"text": "private"})
        self.assertEqual(self.relay_message_count(), before)
        self.assertTrue(self.bob.store.has_received(sent["message_id"]))
        self.assertEqual(self.alice.store.private_messages(self.bob.identity.agent_id)[0]["direction"], "outgoing")
        self.assertEqual(self.bob.store.private_messages(self.alice.identity.agent_id)[0]["direction"], "incoming")

    def test_private_messages_http_history_is_local(self):
        self.alice.request_peer_trust(self.bob.identity.agent_id)
        self.bob.peer_trust_notifications(); self.bob.respond_peer_trust(self.alice.identity.agent_id, "accept"); self.alice.peer_trust_notifications()
        sent = self.alice.send_routed({"kind": "private", "recipient_id": self.bob.identity.agent_id}, {"type": "chat.message", "payload": {"text": "history"}})
        request = Request(f"http://127.0.0.1:{self.bob_server.server_port}/v1/private-messages?peer_id={self.alice.identity.agent_id}")
        with urlopen(request) as response:
            value = json.loads(response.read())
        self.assertEqual(value["messages"][0]["message_id"], sent["deliveries"][0]["message_id"])

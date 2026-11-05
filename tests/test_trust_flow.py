import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from agent_sidecar.protocol import encrypt_message
from agent_sidecar.relay import make_handler as make_relay_handler
from agent_sidecar.service import Sidecar, make_handler as make_sidecar_handler
from agent_sidecar.store import RelayStore
from agent_sidecar.trust import create_trust_statement


class PeerTrustFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.relay_store = RelayStore(self.root / "relay.db")
        self.relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_relay_handler(self.relay_store))
        self.relay_thread = threading.Thread(target=self.relay_server.serve_forever, daemon=True)
        self.relay_thread.start()
        relay_url = f"http://127.0.0.1:{self.relay_server.server_port}"
        self.alice = Sidecar(self.root / "alice", relay_url)
        self.bob = Sidecar(self.root / "bob", relay_url)
        self.carol = Sidecar(self.root / "carol", relay_url)

    def tearDown(self):
        self.alice.store.close(); self.bob.store.close(); self.carol.store.close()
        self.relay_server.shutdown(); self.relay_server.server_close(); self.relay_thread.join(timeout=2)
        self.relay_store.close()
        self.temporary.cleanup()

    def relationship(self, node, peer_id):
        return {item["agent_id"]: item for item in node.peer_candidates()}[peer_id]

    def test_one_way_notification_and_reciprocal_acceptance_form_mutual_trust(self):
        requested = self.alice.request_peer_trust(self.bob.identity.agent_id, self.bob.identity.public().signing_key)
        self.assertEqual(requested["relationship_state"], "trusting")
        self.assertIsNotNone(self.alice.trusted_peer(self.bob.identity.agent_id))
        self.assertIsNone(self.bob.trusted_peer(self.alice.identity.agent_id))

        notifications = self.bob.peer_trust_notifications()
        self.assertEqual(len(notifications["notifications"]), 1)
        self.assertEqual(notifications["notifications"][0]["issuer_agent_id"], self.alice.identity.agent_id)
        self.assertEqual(notifications["notifications"][0]["relationship_state"], "trusted_by_peer")
        self.assertIsNone(self.bob.trusted_peer(self.alice.identity.agent_id))

        accepted = self.bob.respond_peer_trust(self.alice.identity.agent_id, "accept")
        self.assertEqual(accepted["relationship_state"], "mutual")
        alice_notifications = self.alice.peer_trust_notifications()
        self.assertEqual(alice_notifications["notifications"][0]["issuer_agent_id"], self.bob.identity.agent_id)
        self.assertEqual(self.relationship(self.alice, self.bob.identity.agent_id)["relationship_state"], "mutual")
        self.assertEqual(self.relationship(self.bob, self.alice.identity.agent_id)["relationship_state"], "mutual")

        # Trust never propagates to an unrelated third node.
        self.assertEqual(self.relationship(self.alice, self.carol.identity.agent_id)["relationship_state"], "pending")
        self.assertEqual(self.relationship(self.bob, self.carol.identity.agent_id)["relationship_state"], "pending")

    def test_reject_keeps_the_requesters_one_way_trust_without_granting_reverse_trust(self):
        self.alice.request_peer_trust(self.bob.identity.agent_id)
        self.bob.peer_trust_notifications()
        rejected = self.bob.respond_peer_trust(self.alice.identity.agent_id, "reject")
        self.assertEqual(rejected["relationship_state"], "trusted_by_peer")
        self.alice.peer_trust_notifications()
        self.assertEqual(self.relationship(self.alice, self.bob.identity.agent_id)["relationship_state"], "rejected_by_peer")
        self.assertIsNotNone(self.alice.trusted_peer(self.bob.identity.agent_id))
        self.assertIsNone(self.bob.trusted_peer(self.alice.identity.agent_id))

    def test_each_side_can_revoke_its_own_half_of_mutual_trust(self):
        self.alice.request_peer_trust(self.bob.identity.agent_id)
        self.bob.peer_trust_notifications()
        self.bob.respond_peer_trust(self.alice.identity.agent_id, "accept")
        self.alice.peer_trust_notifications()
        revoked = self.alice.revoke_peer_trust(self.bob.identity.agent_id)
        self.assertTrue(revoked["revoked"])
        self.assertEqual(revoked["relationship_state"], "trusted_by_peer")
        self.bob.peer_trust_notifications()
        self.assertEqual(self.relationship(self.bob, self.alice.identity.agent_id)["relationship_state"], "revoked_by_peer")
        self.assertIsNone(self.alice.trusted_peer(self.bob.identity.agent_id))
        self.assertIsNotNone(self.bob.trusted_peer(self.alice.identity.agent_id))

    def test_local_revocation_succeeds_when_peer_is_missing_from_relay_directory(self):
        self.alice.trust(self.bob.identity.agent_id, self.bob.identity.public().signing_key)
        original = self.alice.relay.identity_for
        self.alice.relay.identity_for = lambda _agent_id: (_ for _ in ()).throw(RuntimeError("not found"))
        try:
            result = self.alice.revoke_peer_trust(self.bob.identity.agent_id)
        finally:
            self.alice.relay.identity_for = original
        self.assertTrue(result["revoked"])
        self.assertIn(result["notification_status"], {"sent", "failed"})
        self.assertIsNone(self.alice.trusted_peer(self.bob.identity.agent_id))

    def test_invalid_subject_is_rejected_and_acknowledged(self):
        statement = create_trust_statement(self.alice.identity, self.alice.identity.public(), "trust")
        envelope = encrypt_message(
            self.alice.identity,
            self.bob.identity.public(),
            self.alice.store.next_sequence(self.bob.identity.agent_id),
            "node.trust.statement",
            statement,
        )
        self.alice.relay.send(envelope)
        result = self.bob.peer_trust_notifications()
        self.assertEqual(result["notifications"], [])
        self.assertEqual(len(result["polling"]["rejected"]), 1)
        self.assertEqual(self.bob.relay.inbox(), [])

    def test_duplicate_signed_statement_is_idempotent(self):
        statement = create_trust_statement(self.alice.identity, self.bob.identity.public(), "trust")
        for _ in range(2):
            envelope = encrypt_message(
                self.alice.identity,
                self.bob.identity.public(),
                self.alice.store.next_sequence(self.bob.identity.agent_id),
                "node.trust.statement",
                statement,
            )
            self.alice.relay.send(envelope)
        result = self.bob.peer_trust_notifications()
        self.assertEqual(len(result["notifications"]), 1)
        self.assertEqual(result["polling"]["accepted_statement_ids"], [statement["statement_id"]])
        self.assertEqual(self.bob.relay.inbox(), [])

    def test_http_api_exposes_request_notification_and_acceptance(self):
        alice_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(self.alice))
        bob_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(self.bob))
        alice_thread = threading.Thread(target=alice_server.serve_forever, daemon=True)
        bob_thread = threading.Thread(target=bob_server.serve_forever, daemon=True)
        alice_thread.start(); bob_thread.start()
        alice_url = f"http://127.0.0.1:{alice_server.server_port}"
        bob_url = f"http://127.0.0.1:{bob_server.server_port}"
        try:
            request = Request(
                alice_url + "/v1/peers/trust-requests",
                data=json.dumps({"agent_id": self.bob.identity.agent_id, "expected_signing_key": self.bob.identity.public().signing_key}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                self.assertEqual(json.loads(response.read())["relationship_state"], "trusting")
            with urlopen(bob_url + "/v1/peers/trust-notifications") as response:
                notification = json.loads(response.read())["notifications"][0]
            self.assertEqual(notification["relationship_state"], "trusted_by_peer")
            response_request = Request(
                bob_url + "/v1/peers/trust-response",
                data=json.dumps({"agent_id": self.alice.identity.agent_id, "decision": "accept"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(response_request) as response:
                self.assertEqual(json.loads(response.read())["relationship_state"], "mutual")
            with urlopen(alice_url + "/v1/peers/trust-notifications"):
                pass
            with urlopen(alice_url + "/v1/peers/candidates") as response:
                peers = {item["agent_id"]: item for item in json.loads(response.read())["peers"]}
            self.assertEqual(peers[self.bob.identity.agent_id]["relationship_state"], "mutual")
            revoke_request = Request(
                alice_url + "/v1/peers/trust/" + __import__("urllib.parse", fromlist=["quote"]).quote(self.bob.identity.agent_id, safe=""),
                method="DELETE",
            )
            with urlopen(revoke_request) as response:
                revoked = json.loads(response.read())
            self.assertTrue(revoked["revoked"])
            self.assertIsNone(self.alice.trusted_peer(self.bob.identity.agent_id))
        finally:
            alice_server.shutdown(); alice_server.server_close(); alice_thread.join(timeout=2)
            bob_server.shutdown(); bob_server.server_close(); bob_thread.join(timeout=2)

    def test_normal_inbox_poll_routes_trust_control_before_application_messages(self):
        self.bob.trust(self.alice.identity.agent_id, self.alice.identity.public().signing_key)
        self.alice.request_peer_trust(self.bob.identity.agent_id)
        self.assertEqual(self.bob.receive(), [])
        incoming = self.bob.store.latest_peer_trust_statement(self.alice.identity.agent_id, "incoming")
        self.assertIsNotNone(incoming)
        self.assertEqual(incoming["decision"], "trust")


if __name__ == "__main__":
    unittest.main()

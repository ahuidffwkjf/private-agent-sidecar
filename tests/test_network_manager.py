import hashlib
import json
from pathlib import Path
import secrets
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent_sidecar.crypto import Identity, canonical
from agent_sidecar.network import NetworkLease, NetworkManager, NetworkStore, Tunnel, validate_route
from agent_sidecar.protocol import encrypt_message
from agent_sidecar.service import Sidecar
from agent_sidecar.store import LocalStore


class NetworkManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.alice = Identity.load_or_create(self.root / 'alice.json')
        self.bob = Identity.load_or_create(self.root / 'bob.json')
        self.store = NetworkStore(self.root / 'network.db')
        self.addCleanup(self.store.close)

    def route(self, version=1, **values):
        body = {'protocol': 'sidecar-tailcat-route/1', 'agent_id': self.bob.agent_id,
                'audience': self.alice.agent_id,
                'version': version, 'issued_at': int(time.time()), 'expires_at': int(time.time()) + 1000,
                'port': 8803, 'client_key': 'nodekey:' + 'a' * 64,
                'address': 'tc' + 'x' * 100, 'tls_fingerprint': hashlib.sha256(b'cert').hexdigest()}
        body.update(values)
        return {'route': body, 'signature': self.bob.sign(canonical(body))}

    def test_signed_route_identity_expiry_tamper_and_persistent_rollback(self):
        signed = self.route(2)
        self.store.install_route(signed, self.bob.public())
        self.store.install_route(signed, self.bob.public())
        for value in (self.route(1), self.route(2, port=4444), self.route(3, expires_at=1)):
            with self.assertRaises(ValueError):
                self.store.install_route(value, self.bob.public())
        self.store.install_route(self.route(3), self.bob.public())
        another = NetworkStore(self.root / 'network.db')
        try:
            with self.assertRaises(ValueError):
                another.install_route(signed, self.bob.public())
        finally:
            another.close()
        signed['route']['port'] = 3333
        with self.assertRaises(ValueError):
            validate_route(signed, self.bob.public())
        with self.assertRaises(ValueError):
            validate_route(self.route(), self.alice.public())

    def test_outbox_persists_only_ciphertext_and_reuses_envelope(self):
        envelope = encrypt_message(self.alice, self.bob.public(), 1, 'chat.message', {'text': 'sensitive payload'})
        self.store.enqueue(envelope, self.bob.public())
        row = self.store.due(time.time())[0]
        self.assertNotIn('sensitive payload', row['body'])
        self.assertEqual(json.loads(row['body'])['envelope'], envelope)
        self.store.finish(envelope['message_id'], 'queued', 'unreachable', time.time() + 20)
        self.assertEqual(self.store.due(time.time()), [])
        self.assertEqual(self.store.due(time.time() + 21)[0]['message_id'], envelope['message_id'])
        with self.assertRaises(ValueError):
            self.store.enqueue(encrypt_message(self.alice, self.bob.public(), 2, 'chat', {}), self.bob.public(), max_pending=1)

    def manager(self):
        key = self.root / 'tailcat.private.json'
        key.write_text('{}')
        key.chmod(0o600)
        local = LocalStore(self.root / 'state.db')
        self.addCleanup(local.close)
        sidecar = SimpleNamespace(store=local, identity=self.alice,
                                  trusted_peer=lambda _: self.bob.public(), _private_trust_ready=lambda _: True)
        config = {'tailcat_bin': 'tailcat-test', 'key_file': str(key), 'address_file': str(self.root / 'address'),
                  'peer_port': 8803, 'tls_fingerprint': 'f' * 64}
        with patch('agent_sidecar.network.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='v0.7.0\n')):
            manager = NetworkManager(sidecar, config)
        self.addCleanup(manager.close)
        return manager

    def test_empty_allowlist_is_none_and_revocation_stops_owned_connections(self):
        manager = self.manager()
        with patch.object(Tunnel, 'tick'), patch.object(Tunnel, 'stop') as stop:
            manager.reconcile()
            self.assertIn('--allow=none', manager.server.command)
            manager.store.install_route(self.route(), self.bob.public())
            manager.reconcile()
            self.assertIn('--allow=nodekey:' + 'a' * 64, manager.server.command)
            tunnel = Tunnel(['unused'])
            manager.forwarders[self.bob.agent_id] = {'route': self.route()['route'], 'tunnel': tunnel, 'used': time.monotonic(), 'port': 1}
            manager.sidecar._private_trust_ready = lambda _: False
            manager.reconcile()
            self.assertEqual(manager.forwarders, {})
            self.assertIn('--allow=none', manager.server.command)
            self.assertGreaterEqual(stop.call_count, 2)

    def test_retry_revocation_expiry_and_invalid_ack_fail_closed(self):
        manager = self.manager()
        envelope = encrypt_message(self.alice, self.bob.public(), 1, 'chat.message', {})
        manager.store.enqueue(envelope, self.bob.public())
        with patch.object(manager, 'deliver', side_effect=OSError('secret address must not appear')):
            manager.drain()
        status = manager.store.status()[0]
        self.assertEqual(status['status'], 'queued')
        self.assertNotIn('secret', json.dumps(status))
        manager.store.finish(envelope['message_id'], 'queued')
        manager.sidecar._private_trust_ready = lambda _: False
        with patch.object(manager, 'deliver') as deliver:
            manager.drain()
            deliver.assert_not_called()
        self.assertEqual(manager.store.status()[0]['status'], 'cancelled')

    def test_tls_pin_is_checked_before_request_and_ack_is_bound_to_message(self):
        manager = self.manager()
        entry = {'port': 1234, 'route': self.route()['route'], 'tunnel': Tunnel(['unused'])}
        connection = Mock()
        connection.sock.getpeercert.return_value = b'wrong-cert'
        payload = {'envelope': {'message_id': 'expected'}}
        with patch.object(manager, 'endpoint', return_value=entry), patch('agent_sidecar.network.http.client.HTTPSConnection', return_value=connection):
            with self.assertRaisesRegex(ValueError, 'pin'):
                manager.deliver(self.bob.agent_id, payload)
            connection.request.assert_not_called()
            connection.sock.getpeercert.return_value = b'cert'
            connection.getresponse.return_value.status = 200
            connection.getresponse.return_value.read.return_value = b'{"status":"delivered","message_id":"other"}'
            with self.assertRaisesRegex(ValueError, 'acknowledgement'):
                manager.deliver(self.bob.agent_id, payload)

    def test_tls_health_requires_matching_sidecar_identity(self):
        manager = self.manager()
        entry = {'port': 1234, 'route': self.route()['route'], 'tunnel': Tunnel(['unused'])}
        connection = Mock()
        connection.sock.getpeercert.return_value = b'cert'
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b'{"status":"ok","agent_id":"other"}'
        with patch.object(manager, 'endpoint', return_value=entry), patch('agent_sidecar.network.http.client.HTTPSConnection', return_value=connection):
            self.assertFalse(manager.probe(self.bob.agent_id)['online'])
            connection.getresponse.return_value.read.return_value = json.dumps({'status': 'ok', 'agent_id': self.bob.agent_id}).encode()
            self.assertTrue(manager.probe(self.bob.agent_id)['online'])

    def test_offline_start_is_opt_in_and_does_not_grant_trust(self):
        with patch('agent_sidecar.service.RelayClient') as client, patch('agent_sidecar.service.collect_capabilities', return_value={}):
            client.return_value.register.side_effect = RuntimeError('offline')
            client.return_value.bootstrap.side_effect = RuntimeError('offline')
            client.return_value.publish_broadcast.side_effect = RuntimeError('offline')
            offline = Sidecar(self.root / 'offline-node', 'https://relay.invalid', allow_offline=True)
            try:
                self.assertEqual(offline.store.trusted_ids(), [])
                self.assertEqual(offline.identity.agent_id, Identity.load_or_create(self.root / 'offline-node/identity.json').agent_id)
            finally:
                offline.store.close()
            with self.assertRaises(RuntimeError):
                Sidecar(self.root / 'strict-node', 'https://relay.invalid')

    def test_forward_startup_refusal_does_not_cause_endless_respawn(self):
        manager = self.manager()
        tunnel = Tunnel(['unused'])
        tunnel.started = time.monotonic()
        tunnel.child = Mock()
        tunnel.child.poll.return_value = None
        entry = {'port': 1234, 'route': self.route()['route'], 'tunnel': tunnel}
        connection = Mock()
        connection.connect.side_effect = ConnectionRefusedError()
        with patch.object(manager, 'endpoint', return_value=entry), patch.object(tunnel, 'failed') as failed, patch('agent_sidecar.network.http.client.HTTPSConnection', return_value=connection):
            with self.assertRaises(ConnectionRefusedError):
                manager.deliver(self.bob.agent_id, {'envelope': {}})
            failed.assert_not_called()
            tunnel.started -= 65
            with self.assertRaises(ConnectionRefusedError):
                manager.deliver(self.bob.agent_id, {'envelope': {}})
            failed.assert_not_called()
            with self.assertRaises(ConnectionRefusedError):
                manager.deliver(self.bob.agent_id, {'envelope': {}})
            failed.assert_called_once()

    def test_supervisor_restarts_pending_forward_without_waiting_for_send(self):
        manager = self.manager()
        route = self.route()
        manager.store.install_route(route, self.bob.public())
        envelope = encrypt_message(self.alice, self.bob.public(), 1, 'chat.message', {})
        manager.store.enqueue(envelope, self.bob.public())
        tunnel = Tunnel(['unused'])
        manager.forwarders[self.bob.agent_id] = {'route': route['route'], 'tunnel': tunnel, 'used': time.monotonic()-200}
        with patch.object(tunnel, 'tick') as tick, patch.object(tunnel, 'stop') as stop, patch.object(Tunnel, 'tick'):
            manager.reconcile()
            self.assertIn(self.bob.agent_id, manager.forwarders)
            tick.assert_called_once()
            stop.assert_not_called()

    def test_process_backoff_and_clean_shutdown(self):
        tunnel = Tunnel([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            tunnel.tick(100)
            self.assertIsNone(tunnel.child.poll())
            tunnel.failed(101)
            self.assertGreater(tunnel.next_attempt, 101)
            tunnel.tick(101)
            self.assertIsNone(tunnel.child)
        finally:
            tunnel.stop()

    def test_worker_stops_transport_on_supervisor_pipe_eof(self):
        import subprocess
        result = subprocess.run([sys.executable, '-m', 'agent_sidecar.tailcat_worker'],
                                input=json.dumps([sys.executable, '-c', 'import time;time.sleep(60)'])+'\n',
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_single_manager_lease_can_be_reacquired_after_close(self):
        lease = NetworkLease(self.root / 'test.lock')
        try:
            with self.assertRaises(ValueError):
                NetworkLease(self.root / 'test.lock')
        finally:
            lease.close()
        NetworkLease(self.root / 'test.lock').close()

    def receiver(self):
        receiver = Sidecar.__new__(Sidecar)
        receiver.identity = self.bob
        receiver.store = LocalStore(self.root / 'receiver.db')
        self.addCleanup(receiver.store.close)
        receiver.trusted_peer = lambda *args: self.alice.public()
        receiver._private_trust_ready = lambda *args: True
        return receiver

    def test_lost_ack_retry_is_idempotent_and_revoked_duplicate_is_rejected(self):
        receiver = self.receiver()
        envelope = encrypt_message(self.alice, self.bob.public(), 1, 'chat.message', {'text': 'hello'})
        body = {'scope': 'private', 'envelope': envelope}
        self.assertEqual(receiver.receive_direct(body)['status'], 'delivered')
        self.assertEqual(receiver.receive_direct(body)['status'], 'duplicate')
        self.assertEqual(len(receiver.store.private_messages()), 1)
        receiver._private_trust_ready = lambda *args: False
        with self.assertRaises(ValueError):
            receiver.receive_direct(body)
        receiver._private_trust_ready = lambda *args: True
        body['envelope']['ciphertext'] = 'invalid'
        with self.assertRaises(ValueError):
            receiver.receive_direct(body)

    def test_managed_send_returns_queued_without_a_network_attempt(self):
        manager = self.manager()
        sender = Sidecar.__new__(Sidecar)
        sender.identity = self.alice
        sender.store = manager.sidecar.store
        sender.trusted_peer = lambda *args: self.bob.public()
        sender._private_trust_ready = lambda *args: True
        sender.network_manager = manager
        with patch.object(manager, 'deliver') as deliver:
            result = sender.send_direct(self.bob.agent_id, 'chat.message', {'text': 'queued'})
            deliver.assert_not_called()
        self.assertEqual(result['status'], 'queued')
        self.assertEqual(manager.store.status()[0]['message_id'], result['message_id'])

    def test_queue_history_and_receipt_transactions_roll_back_together(self):
        manager = self.manager()
        envelope = encrypt_message(self.alice, self.bob.public(), 1, 'chat.message', {})
        manager.sidecar.store.save_private_message(envelope['message_id'], self.bob.agent_id, 'outgoing', 'chat.message', {}, envelope['created_at'])
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            manager.store.enqueue(envelope, self.bob.public(), history={'type': 'chat.message', 'payload': {}})
        self.assertEqual(manager.store.status(), [])
        receiver = self.receiver()
        receiver.store.db.execute("CREATE TRIGGER fail_history BEFORE INSERT ON private_messages BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            receiver.receive_direct({'scope': 'private', 'envelope': envelope})
        self.assertFalse(receiver.store.has_received(envelope['message_id']))

    def test_queued_expiry_is_terminal_and_history_tracks_delivery(self):
        manager = self.manager()
        envelope = encrypt_message(self.alice, self.bob.public(), 1, 'chat.message', {}, ttl_seconds=-1)
        manager.store.enqueue(envelope, self.bob.public(), history={'type': 'chat.message', 'payload': {}})
        with patch.object(manager, 'deliver') as deliver:
            manager.drain()
            deliver.assert_not_called()
        self.assertEqual(manager.store.status()[0]['status'], 'expired')
        self.assertEqual(manager.sidecar.store.private_messages()[0]['status'], 'expired')

    def test_durable_delivery_survives_reopened_local_store(self):
        local = LocalStore(self.root / 'restart.db')
        queue = NetworkStore(local_store=local)
        envelope = encrypt_message(self.alice, self.bob.public(), 1, 'chat.message', {'text': 'restart'})
        queue.enqueue(envelope, self.bob.public(), history={'type': 'chat.message', 'payload': {'text': 'restart'}})
        local.close()
        restored = LocalStore(self.root / 'restart.db')
        try:
            durable = NetworkStore(local_store=restored)
            self.assertEqual(json.loads(durable.due(time.time())[0]['body'])['envelope'], envelope)
            durable.finish(envelope['message_id'], 'delivered')
            self.assertEqual(restored.private_messages()[0]['status'], 'delivered')
        finally:
            restored.close()

    def test_import_requires_trust_and_expired_routes_close_forwarders(self):
        manager = self.manager()
        manager.sidecar._private_trust_ready = lambda _: False
        with self.assertRaisesRegex(ValueError, 'trust'):
            manager.import_route(self.route())
        manager.sidecar._private_trust_ready = lambda _: True
        with self.assertRaisesRegex(ValueError, 'another node'):
            manager.import_route(self.route(audience=Identity.load_or_create(self.root / 'other.json').agent_id))
        with patch.object(Tunnel, 'tick'), patch.object(Tunnel, 'stop') as stop:
            route = self.route(expires_at=int(time.time())+2)
            manager.import_route(route)
            manager.forwarders[self.bob.agent_id] = {'route': route['route'], 'tunnel': Tunnel(['unused']), 'used': time.monotonic()}
            with patch('agent_sidecar.network.time.time', return_value=time.time()+10):
                manager.reconcile()
            self.assertFalse(manager.forwarders)
            self.assertIn('--allow=none', manager.server.command)
            self.assertTrue(stop.called)

    def test_outbound_process_uses_dedicated_peer_key_not_server_key(self):
        manager = self.manager()
        client = self.root / 'outbound.private.json'
        client.write_text('{}')
        client.chmod(0o600)
        manager.config['client_keys'] = {self.bob.agent_id: str(client)}
        manager.store.install_route(self.route(), self.bob.public())
        with patch.object(Tunnel, 'tick'), patch('socket.socket') as socket_factory:
            socket_factory.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 12345)
            entry = manager.endpoint(self.bob.agent_id)
            self.assertIn('--key=' + str(client), entry['tunnel'].command)
            self.assertNotIn('--key=' + str(manager.key), entry['tunnel'].command)

    def test_export_version_watermark_survives_restart(self):
        signed = self.route(5)
        self.store.record_export(signed)
        with self.assertRaises(ValueError):
            self.store.record_export(self.route(4))
        self.store.record_export(self.route(6))


class PeerEndpointIntegrationTests(unittest.TestCase):
    def test_tls_peer_listener_denies_admin_and_authenticates_before_duplicate_ack(self):
        import datetime
        import ipaddress
        import ssl
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        from agent_sidecar.http_utils import enable_tls
        from agent_sidecar.network import peer_only_handler
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alice = Identity.load_or_create(root / 'alice.json')
            bob = Identity.load_or_create(root / 'bob.json')
            sidecar = Sidecar.__new__(Sidecar)
            sidecar.identity = bob
            sidecar.capabilities = {}
            sidecar.store = LocalStore(root / 'state.db')
            sidecar.trusted_peer = lambda *args: alice.public()
            sidecar._private_trust_ready = lambda *args: True
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, '127.0.0.1')])
            now = datetime.datetime.now(datetime.timezone.utc)
            certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                           .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
                           .not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False).sign(key, hashes.SHA256()))
            cert_path, key_path = root / 'cert.pem', root / 'key.pem'
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            server = ThreadingHTTPServer(('127.0.0.1', 0), peer_only_handler(sidecar))
            enable_tls(server, cert_path, key_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            context = ssl.create_default_context(cafile=str(cert_path))
            base = f'https://127.0.0.1:{server.server_port}'
            try:
                for method, path in [('GET', '/ui'), ('GET', '/v1/identity'), ('POST', '/v1/network/routes/import'), ('PUT', '/v1/peers/trust'), ('DELETE', '/v1/peers/trust/x')]:
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(Request(base+path, method=method, data=b'{}' if method!='GET' else None), context=context)
                    self.assertEqual(caught.exception.code, 404)
                envelope = encrypt_message(alice, bob.public(), 1, 'chat.message', {'text': 'test'})
                body = json.dumps({'scope': 'private', 'envelope': envelope}).encode()
                for expected in ('delivered', 'duplicate'):
                    with urlopen(Request(base+'/v1/direct/messages', data=body, headers={'Content-Type': 'application/json'}), context=context) as response:
                        self.assertEqual(json.load(response)['status'], expected)
                self.assertEqual(len(sidecar.store.private_messages()), 1)
                sidecar._private_trust_ready = lambda *args: False
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(base+'/v1/direct/messages', data=body), context=context)
                self.assertEqual(caught.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                sidecar.store.close()


if __name__ == '__main__':
    unittest.main()

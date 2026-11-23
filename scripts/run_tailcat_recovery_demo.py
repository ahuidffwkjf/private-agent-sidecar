"""Three isolated nodes through real Tailcat v0.7.0; no existing keys or services."""
import argparse
import contextlib
import datetime
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from agent_sidecar.http_utils import enable_tls
from agent_sidecar.network import NetworkManager, peer_only_handler
from agent_sidecar.relay import make_handler
from agent_sidecar.service import Sidecar
from agent_sidecar.store import RelayStore


def wait(predicate, seconds=240):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.25)
    raise TimeoutError('recovery scenario deadline exceeded')


def certificate(root):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, '127.0.0.1')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
            .not_valid_after(now+datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .sign(key, hashes.SHA256()))
    public, private = root / 'tls.pem', root / 'tls-key.pem'
    public.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public.chmod(0o600)
    private.chmod(0o600)
    return public, private, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def run(binary, progress):
    managers, servers, stores = [], [], []
    def serve(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server
    with tempfile.TemporaryDirectory(prefix='tailcat-recovery-') as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        try:
            relay_store = RelayStore(root / 'relay.db')
            stores.append(relay_store)
            relay = serve(ThreadingHTTPServer(('127.0.0.1', 0), make_handler(relay_store)))
            nodes = []
            for index in range(3):
                progress['phase'] = 'initialize-node-' + str(index)
                directory = root / str(index)
                directory.mkdir(mode=0o700)
                key_file = directory / 'tailcat.private.json'
                result = subprocess.run([binary, 'genkey', f'--key={key_file}', '--fixed-region'], capture_output=True, timeout=60)
                if result.returncode:
                    raise RuntimeError('Tailcat key initialization failed (raw output suppressed)')
                key_file.chmod(0o600)
                public, private, pin = certificate(directory)
                node = Sidecar(directory, f'http://127.0.0.1:{relay.server_port}')
                stores.append(node.store)
                server = ThreadingHTTPServer(('127.0.0.1', 0), peer_only_handler(node))
                enable_tls(server, public, private)
                serve(server)
                config = {'tailcat_bin': binary, 'key_file': str(key_file), 'peer_port': server.server_port,
                          'address_file': str(directory / 'address'), 'tls_fingerprint': pin}
                manager = NetworkManager(node, config)
                managers.append(manager)
                node.network_manager = manager
                manager.start()
                nodes.append(node)
            for a in nodes:
                for b in nodes:
                    if a is not b:
                        a.trust(b.identity.agent_id, b.identity.public().signing_key)
            progress['phase'] = 'wait-tailcat-server-addresses'
            wait(lambda: all(Path(m.config['address_file']).is_file() for m in managers), seconds=45)
            progress['phase'] = 'exchange-signed-routes'
            for i, manager in enumerate(managers):
                manager.config['client_keys'] = {}
                for j, node in enumerate(nodes):
                    if i != j:
                        key_path = root / str(i) / f'client-{j}.private.json'
                        generated = subprocess.run([binary, 'genkey', f'--key={key_path}', '--client'], capture_output=True, timeout=20)
                        if generated.returncode:
                            raise RuntimeError('outbound key generation failed')
                        key_path.chmod(0o600)
                        manager.config['client_keys'][node.identity.agent_id] = str(key_path)
            for i, manager in enumerate(managers):
                for j, target in enumerate(managers):
                    if i != j:
                        target.import_route(manager.export_route(j + 1, nodes[j].identity.agent_id))
            ids = []
            progress['phase'] = 'initial-three-node-delivery'
            for i, node in enumerate(nodes):
                for j, target in enumerate(nodes):
                    if i != j:
                        ids.append((managers[i], node.send_direct(target.identity.agent_id, 'chat.message', {'text': f'initial-{i}-{j}'})['message_id']))
            wait(lambda: all(any(r['message_id']==mid and r['status']=='delivered' for r in m.store.status()) for m,mid in ids))
            # A real Tailcat server process dies; supervisor must recreate it using the saved identity.
            old_pid = managers[1].server.child.pid
            progress['phase'] = 'server-process-recovery'
            managers[1].server.child.stdin.write(b'kill\n')
            managers[1].server.child.stdin.flush()
            wait(lambda: managers[1].server.child is not None and managers[1].server.child.poll() is None and managers[1].server.child.pid != old_pid)
            retry = nodes[0].send_direct(nodes[1].identity.agent_id, 'chat.message', {'text': 'after server restart'})['message_id']
            wait(lambda: any(r['message_id']==retry and r['status']=='delivered' for r in managers[0].store.status()))
            # Lose the HTTP acknowledgement after the receiver persisted the message.
            original = managers[0].deliver
            progress['phase'] = 'lost-ack-recovery'
            dropped = []
            def drop_ack(agent_id, payload):
                result = original(agent_id, payload)
                if not dropped:
                    dropped.append(payload['envelope']['message_id'])
                    raise OSError('simulated lost acknowledgement')
                return result
            managers[0].deliver = drop_ack
            duplicate = nodes[0].send_direct(nodes[1].identity.agent_id, 'chat.message', {'text': 'ack recovery'})['message_id']
            wait(lambda: any(r['message_id']==duplicate and r['status']=='delivered' for r in managers[0].store.status()))
            assert sum(m['message_id']==duplicate for m in nodes[1].store.private_messages()) == 1
            assert relay_store.db.execute('SELECT count(*) FROM messages').fetchone()[0] == 0
            return {'status': 'passed', 'nodes': 3, 'real_tailcat': True,
                    'checks': ['three-node-TLS-delivery', 'server-process-restart', 'lost-ack-idempotent-retry', 'no-private-Relay-fallback'],
                    'path': 'Tailcat-selected; direct-versus-DERP not asserted'}
        finally:
            progress['diagnostics'] = [{'server_running': m.status()['server_running'], 'server_last_exit': m.status()['server_last_exit'],
                                       'errors': m.errors, 'address_created': Path(m.config['address_file']).exists(),
                                       'outbox_states': [r['status'] for r in m.store.status()],
                                       'transport_errors': [p['last_error'] for p in m.status()['peers']]} for m in managers]
            for manager in managers:
                manager.close()
            for server, thread in reversed(servers):
                server.shutdown()
                server.server_close()
                thread.join()
            for store in reversed(stores):
                store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tailcat', default='tailcat')
    parser.add_argument('--output', type=Path, default=Path('runs/tailcat-recovery'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = None
    progress = {}
    with (args.output / 'local-http.log').open('w') as log, contextlib.redirect_stdout(log):
        try:
            result = run(args.tailcat, progress)
        except Exception as exc:
            result = {'status': 'failed', 'error_type': type(exc).__name__, 'real_tailcat': True, **progress}
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))
    raise SystemExit(0 if result['status']=='passed' else 1)

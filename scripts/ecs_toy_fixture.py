"""Synthetic loopback TLS Relay for desktop acceptance, never a public deployment."""
import datetime
import ipaddress
import json
from pathlib import Path
import secrets
import ssl
import sys
import threading
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from agent_sidecar.desktop import LoopbackHTTPServer
from agent_sidecar.relay import make_handler
from agent_sidecar.store import RelayStore


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic local ECS test')])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(True, False, True, False, False, True, True, False, False), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                .sign(key, hashes.SHA256()))
        pem = serialization.Encoding.PEM
        (self.root / 'cert.pem').write_bytes(cert.public_bytes(pem))
        (self.root / 'key.pem').write_bytes(key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (self.root / 'key.pem').chmod(0o600)
        token = secrets.token_urlsafe(48)
        self.store = RelayStore(self.root / 'relay.db')
        handler = make_handler(self.store, token)
        handler.log_message = lambda *args: None
        self.server = LoopbackHTTPServer(('127.0.0.1', 0), handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.root / 'cert.pem', self.root / 'key.pem')
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
        self.thread.start()
        self.access_file = self.root / 'access.private.json'
        self.access_file.write_text(json.dumps({'schema': 'sidecar-ecs-access/1',
            'relay_url': 'https://127.0.0.1:' + str(self.server.server_port), 'relay_token': token,
            'ca_pem': cert.public_bytes(pem).decode(), 'experiment_id': 'synthetic-ecs-test'}))
        self.access_file.chmod(0o600)

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.store.close()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='sidecar-ecs-fixture-') as root:
        fixture = Fixture(root)
        print(json.dumps({'access_file': str(fixture.access_file), 'synthetic': True}), flush=True)
        try:
            sys.stdin.read()
        finally:
            fixture.close()

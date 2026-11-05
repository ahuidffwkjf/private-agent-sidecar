"""Opt-in real Tailcat local acceptance; creates no production configuration."""
import argparse
import contextlib
import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sidecar_setup import run, write_private, write_json, json_read, local_api, SetupError
from package_sidecar_installer import package


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tailcat', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    children = []
    temporary = tempfile.TemporaryDirectory(prefix='sidecar-clean-install-')
    phase = 'package'
    result = {'scope': 'real Tailcat, local two-process tunnel; not cross-machine NAT acceptance'}
    try:
        with contextlib.nullcontext():
            root = Path(temporary.name)
            root.chmod(0o700)
            archive = root / 'installer.zip'
            package(archive)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(root / 'unpacked')
            source = root / 'unpacked/sidecar-agent-installer'
            setup = source / 'scripts/sidecar_setup.py'
            home = root / 'installed'
            phase = 'install-from-extracted-package'
            output = json.loads(run([sys.executable, str(setup), '--home', str(home),
                                     'install', '--tailcat', str(args.tailcat)], timeout=600))
            result['install'] = output['status']
            metadata = json_read(home / 'installed.json')
            request = json_read(home / 'enrollment.json')
            identity_before = (home / 'node/identity.json').read_bytes()
            run([sys.executable, str(setup), '--home', str(home), 'install', '--tailcat', str(args.tailcat)])
            assert (home / 'node/identity.json').read_bytes() == identity_before
            result['repeat_install_preserves_identity'] = True
            result['skills'] = json.loads(run([sys.executable, str(setup), '--home', str(home),
                                              'skills', '--target', str(root / 'agent-skills')]))['count']
            phase = 'local-relay-tls'
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, '127.0.0.1')])
            now = datetime.datetime.now(datetime.timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
                    .not_valid_after(now + datetime.timedelta(days=1))
                    .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                    .sign(key, hashes.SHA256()))
            cert_file, key_file = root / 'cert.pem', root / 'tls-key.pem'
            write_private(cert_file, cert.public_bytes(serialization.Encoding.PEM))
            write_private(key_file, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            token_file = root / 'relay-token'
            write_private(token_file, os.urandom(32).hex())
            relay_port, local_port, node_port = port(), port(), port()
            env = dict(os.environ, RELAY_TLS_CERT=str(cert_file), RELAY_TLS_KEY=str(key_file),
                       RELAY_DATABASE=str(root / 'relay.db'), RELAY_TOKEN_FILE=str(token_file),
                       TAILCAT_RELAY_PORT=str(relay_port), NO_PROXY='127.0.0.1,localhost')
            deploy = source / 'scripts/tailcat_deploy.py'
            def start(command, environment):
                child = subprocess.Popen(command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                children.append(child)
                return child
            start([metadata['python'], str(deploy), 'relay'], env)
            phase = 'tailcat-server-key'
            server_key = root / 'server.private.json'
            run([str(args.tailcat), 'genkey', f'--key={server_key}', '--fixed-region'], timeout=90)
            address_file = root / 'server-address'
            env.update(TAILCAT_BIN=str(args.tailcat), TAILCAT_KEY_FILE=str(server_key),
                       TAILCAT_CLIENT_KEYS=request['tailcat_client_public_key'], TAILCAT_ADDR_FILE=str(address_file))
            phase = 'tailcat-server'
            server = start([metadata['python'], str(deploy), 'serve'], env)
            deadline = time.monotonic() + 60
            while not address_file.exists() and time.monotonic() < deadline and server.poll() is None:
                time.sleep(0.3)
            if not address_file.exists():
                raise SetupError('Tailcat server did not create its address file')
            phase = 'configure-device'
            access = root / 'access.json'
            issued = json.loads(run([sys.executable, str(source / 'scripts/issue_sidecar_access.py'),
                '--request', str(home / 'enrollment.json'), '--address-file', str(address_file),
                '--token-file', str(token_file), '--ca-file', str(cert_file), '--output', str(access),
                '--relay-port', str(relay_port), '--local-port', str(local_port),
                '--node-port', str(node_port), '--approved']))
            assert issued['sha256'] == hashlib.sha256(access.read_bytes()).hexdigest()
            result['administrator_issue_bundle'] = 'passed'
            run([sys.executable, str(setup), '--home', str(home), 'configure', '--access', str(access),
                 '--sha256', hashlib.sha256(access.read_bytes()).hexdigest()])
            phase = 'open-and-verify'
            opened = start([sys.executable, str(setup), '--home', str(home), 'open'], os.environ.copy())
            deadline = time.monotonic() + 65
            connected = False
            while time.monotonic() < deadline and opened.poll() is None:
                session = home / 'session.json'
                if session.exists() and json_read(session).get('status') == 'connected':
                    connected = True
                    break
                time.sleep(0.5)
            if not connected:
                raise SetupError('Open did not reach connected state')
            assert local_api(node_port, '/v1/identity')['agent_id'] == metadata['agent_id']
            from urllib.request import ProxyHandler, build_opener
            with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{node_port}/', timeout=5) as response:
                assert 'Sidecar' in response.read().decode('utf-8')
            result['dashboard_http'] = 'passed'
            result['real_tailcat_tls_registration'] = 'passed'
            phase = 'clean-shutdown'
            opened.send_signal(signal.SIGTERM)
            opened.wait(timeout=20)
            assert json_read(home / 'session.json')['status'] == 'stopped'
            assert (home / 'node/identity.json').read_bytes() == identity_before
            result['shutdown_preserves_identity'] = True
            result['status'] = 'passed'
    except Exception as exc:
        result.update(status='failed', phase=phase, error=type(exc).__name__)
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        temporary.cleanup()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.report, result)
        print(json.dumps(result))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    os.umask(0o077)
    raise SystemExit(main())

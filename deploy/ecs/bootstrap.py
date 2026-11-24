"""First installation only. Run as root with a verified, secret-free source archive."""
import argparse
import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import tarfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def run(*args):
    subprocess.run(args, check=True)


def write(path, content, mode=0o600, owner=None):
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    path.chmod(mode)
    if owner:
        os.chown(path, owner.pw_uid, owner.pw_gid)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--archive', required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--address', required=True)
    args = p.parse_args()
    address = ipaddress.IPv4Address(args.address)
    assert os.getuid() == 0
    archive = Path(args.archive)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == args.sha256
    root, config, state = map(Path, ['/opt/sidecar-ecs', '/etc/sidecar-ecs', '/var/lib/sidecar-ecs'])
    if any(path.exists() for path in (root, config, state)):
        raise RuntimeError('Existing ECS deployment found; use an explicit upgrade, never overwrite state')
    os.umask(0o077)
    try:
        owner = pwd.getpwnam('sidecar-ecs')
    except KeyError:
        run('useradd', '--system', '--home-dir', str(state), '--shell', '/usr/sbin/nologin', 'sidecar-ecs')
        owner = pwd.getpwnam('sidecar-ecs')
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    source = root / 'releases' / 'toy-0.3.3'
    source.mkdir(parents=True, mode=0o755)
    source.parent.chmod(0o755)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('unsafe source archive member')
        tar.extractall(source)
    for directory, _, files in os.walk(source):
        Path(directory).chmod(0o755)
        for name in files:
            (Path(directory) / name).chmod(0o644)
    (root / 'current').symlink_to(source)
    run('python3', '-m', 'venv', str(root / 'venv'))
    run(str(root / 'venv/bin/python'), '-m', 'pip', 'install', '--disable-pip-version-check', 'cryptography==50.0.1')
    for directory, _, files in os.walk(root / 'venv'):
        Path(directory).chmod(0o755)
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                path.chmod(0o755 if os.access(path, os.X_OK) else 0o644)
    config.mkdir(mode=0o750)
    config.chmod(0o750)
    os.chown(config, 0, owner.pw_gid)
    state.mkdir(mode=0o700)
    os.chown(state, owner.pw_uid, owner.pw_gid)
    now = datetime.datetime.utcnow()
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Sidecar ECS Experiment CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
          .not_valid_after(now + datetime.timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    certificate = (x509.CertificateBuilder()
          .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(address))]))
          .issuer_name(ca_name).public_key(server_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=180))
          .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
          .add_extension(x509.SubjectAlternativeName([x509.IPAddress(address), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
          .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
          .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    write(config / 'ca-key.pem', ca_key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    write(config / 'ca.pem', ca.public_bytes(pem), 0o644)
    write(config / 'server.pem', certificate.public_bytes(pem), 0o644)
    write(config / 'server-key.pem', server_key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()), owner=owner)
    token = secrets.token_urlsafe(48)
    write(config / 'relay-token', token + '\n', owner=owner)
    settings = dict(host='0.0.0.0', port=8787, database=str(state / 'relay.db'),
                    tls_cert=str(config / 'server.pem'), tls_key=str(config / 'server-key.pem'),
                    token_file=str(config / 'relay-token'), rate_limit_per_minute=1000)
    write(config / 'relay.json', json.dumps(settings, indent=2) + '\n', 0o640, owner)
    access = {'schema': 'sidecar-ecs-access/1', 'relay_url': 'https://' + str(address) + ':8787',
              'relay_token': token, 'ca_pem': ca.public_bytes(pem).decode(),
              'experiment_id': 'ecs-toy-20261124'}
    write(config / 'client-access.private.json', json.dumps(access, indent=2) + '\n')
    run('runuser', '-u', 'sidecar-ecs', '--', str(root / 'venv/bin/python'),
        str(source / 'scripts/ecs_relay.py'), '--config', str(config / 'relay.json'), '--check')
    shutil.copy2(source / 'deploy/ecs/sidecar-ecs-relay.service', '/etc/systemd/system/sidecar-ecs-relay.service')
    Path('/etc/systemd/system/sidecar-ecs-relay.service').chmod(0o644)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'sidecar-ecs-relay.service')
    print(json.dumps({'installed': True, 'port': 8787, 'tls': True, 'source_sha256': args.sha256}))


if __name__ == '__main__':
    main()

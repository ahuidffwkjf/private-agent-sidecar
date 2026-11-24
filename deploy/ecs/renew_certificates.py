"""Reissue certificates with complete RFC 5280 extensions, retaining key material."""
import datetime
import ipaddress
import json
import os
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


def main():
    assert os.getuid() == 0
    root = Path('/etc/sidecar-ecs')
    ca_key = serialization.load_pem_private_key((root / 'ca-key.pem').read_bytes(), password=None)
    server_key = serialization.load_pem_private_key((root / 'server-key.pem').read_bytes(), password=None)
    old_ca = x509.load_pem_x509_certificate((root / 'ca.pem').read_bytes())
    now = datetime.datetime.utcnow()
    ca = (x509.CertificateBuilder().subject_name(old_ca.subject).issuer_name(old_ca.subject)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
          .sign(ca_key, hashes.SHA256()))
    server = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, '189.24.102.37')]))
          .issuer_name(ca.subject).public_key(server_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=180))
          .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
          .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
          .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(a)) for a in ['189.24.102.37','127.0.0.1']]), False)
          .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
          .sign(ca_key, hashes.SHA256()))
    for filename, cert in [('ca.pem', ca), ('server.pem', server)]:
        temporary = root / (filename + '.tmp')
        temporary.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        temporary.chmod(0o644)
        temporary.replace(root / filename)
    access_file = root / 'client-access.private.json'
    access = json.loads(access_file.read_text())
    access['ca_pem'] = (root / 'ca.pem').read_text()
    temporary = access_file.with_suffix('.tmp')
    temporary.write_text(json.dumps(access, indent=2))
    temporary.chmod(0o600)
    temporary.replace(access_file)
    print('Certificates reissued; private keys and Relay token unchanged')


if __name__ == '__main__':
    main()

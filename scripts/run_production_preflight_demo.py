"""Generate a disposable TLS pair and validate the production preflight gate."""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agent_sidecar.preflight import run_preflight


def write_helper(path: Path) -> None:
    path.write_text("#!/bin/sh\ncat >/dev/null\nprintf '{\"ok\":true}'\n", "utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def write_cert(cert_path: Path, key_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "node.example")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        .not_valid_after(__import__("datetime").datetime.now(__import__("datetime").timezone.utc) + __import__("datetime").timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    cert_path.chmod(0o600); key_path.chmod(0o600)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="sidecar-preflight-") as temporary:
        root = Path(temporary)
        cert = root / "cert.pem"; key = root / "key.pem"; helper = root / "key-helper"
        write_cert(cert, key); write_helper(helper)
        report = run_preflight(
            relay_url="https://relay.example:8787",
            direct_endpoint="https://node.example:8801",
            tls_cert=cert,
            tls_key=key,
            project_key_backend="external",
            project_key_command=[str(helper)],
            production=True,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report.get("status") != "ready":
            raise SystemExit(1)

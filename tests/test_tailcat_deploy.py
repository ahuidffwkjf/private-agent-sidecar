import contextlib
import datetime
import importlib.util
import io
import ipaddress
import json
import os
from pathlib import Path
import select
import socket
import socketserver
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agent_sidecar.http_utils import enable_tls
from agent_sidecar.relay import make_handler
from agent_sidecar.store import RelayStore

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tailcat_deploy", ROOT / "scripts/tailcat_deploy.py")
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


class TailcatDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.key = self.root / "client.private.json"
        self.key.write_text("{}")
        self.key.chmod(0o600)
        self.address = self.root / "tailcat-server-address"
        self.address.write_text("tc" + "x" * 100 + "\n")
        self.address.chmod(0o600)
        self.token = self.root / "relay-token"
        self.token.write_text("test-only-relay-secret")
        self.token.chmod(0o600)
        self.env = {
            "TAILCAT_BIN": "tailcat",
            "TAILCAT_KEY_FILE": str(self.key),
            "TAILCAT_ADDR_FILE": str(self.address),
            "TAILCAT_CLIENT_KEYS": "nodekey:" + "a" * 64,
            "RELAY_TOKEN_FILE": str(self.token),
        }
        self.version = patch.object(deploy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "v0.7.0\n", ""))
        self.mock_run = self.version.start()
        self.addCleanup(self.version.stop)

    def certificate(self, san="127.0.0.1"):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, san)])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=1))
                .not_valid_after(now + datetime.timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(san))]), critical=False)
                .sign(key, hashes.SHA256()))
        cert_file, key_file = self.root / "cert.pem", self.root / "tls-key.pem"
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        key_file.chmod(0o600)
        self.env.update(RELAY_TLS_CERT=str(cert_file), RELAY_TLS_KEY=str(key_file),
                        RELAY_CA_FILE=str(cert_file), RELAY_DATABASE=str(self.root / "relay.db"))
        return cert_file, key_file

    def test_server_exposes_only_relay_with_explicit_key_and_allowlist(self):
        cmd = deploy.tailcat_command("serve", self.env)
        self.assertEqual(cmd, ["tailcat", "serve", f"--key={self.key}",
                               "--allow=" + self.env["TAILCAT_CLIENT_KEYS"], "8787"])
        self.assertNotIn("8788", " ".join(cmd))
        for value in ("", "all", "nodekey:placeholder", "nodekey:" + "a" * 64 + ","):
            with self.subTest(value=value), self.assertRaises(ValueError):
                deploy.tailcat_command("serve", dict(self.env, TAILCAT_CLIENT_KEYS=value))

    def test_forward_is_loopback_and_preserves_mapping(self):
        cmd = deploy.tailcat_command("forward", self.env)
        self.assertEqual(cmd[-1], "18787:8787")
        self.assertIn("--bind=127.0.0.1", cmd)
        self.assertEqual(cmd[-2], self.address.read_text().strip())
        custom = deploy.tailcat_command("forward", dict(self.env, TAILCAT_LOCAL_PORT="28787", TAILCAT_RELAY_PORT="28789"))
        self.assertEqual(custom[-1], "28787:28789")

    def test_missing_public_or_malformed_secret_files_fail_closed(self):
        for value in ("", "tcshort", "tc" + "a" * 80 + "\nsecond-line"):
            self.address.write_text(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                deploy.tailcat_command("forward", self.env)
        self.address.chmod(0o644)
        with self.assertRaises(ValueError):
            deploy.tailcat_command("forward", self.env)
        self.address.unlink()
        self.address.symlink_to(self.key)
        with self.assertRaises(ValueError):
            deploy.tailcat_command("forward", self.env)

    def test_version_and_ports_are_validated(self):
        self.mock_run.return_value.stdout = "v0.8.0"
        with self.assertRaises(ValueError):
            deploy.tailcat_command("forward", self.env)
        self.mock_run.return_value.stdout = "v0.7.0"
        for value in ("0", "65536", "8787,8788", "-1", "all"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                deploy.tailcat_command("serve", dict(self.env, TAILCAT_RELAY_PORT=value))

    def test_check_does_not_start_process_or_disclose_address(self):
        output = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch.object(deploy.subprocess, "Popen") as popen:
            with contextlib.redirect_stdout(output):
                self.assertEqual(deploy.main(["forward", "--check"]), 0)
            popen.assert_not_called()
        value = json.loads(output.getvalue())
        self.assertTrue(value["check_only"])
        self.assertNotIn(self.address.read_text().strip(), output.getvalue())
        self.assertNotIn(self.token.read_text(), output.getvalue())

    def test_relay_requires_tls_token_and_loopback(self):
        self.certificate()
        config = deploy.relay_config(self.env)
        self.assertEqual(config["host"], "127.0.0.1")
        self.assertEqual(config["access_token"], self.token.read_text())
        self.assertEqual(config["rate_limit_per_minute"], 120)
        with self.assertRaises(ValueError):
            deploy.relay_config(dict(self.env, RELAY_TLS_KEY=""))
        with self.assertRaises(ValueError):
            deploy.relay_config(dict(self.env, RELAY_RATE_LIMIT="0"))

    def test_node_uses_https_forward_and_preserves_supplied_identity_directory(self):
        self.certificate()
        env = dict(self.env, SIDECAR_DATA_DIR=str(self.root / "node"))
        config = deploy.node_config(env)
        self.assertEqual(config["relay_url"], "https://127.0.0.1:18787")
        self.assertEqual(config["host"], "127.0.0.1")
        self.assertEqual(config["data_dir"], self.root / "node")
        self.assertEqual(config["relay_token"], self.token.read_text())
        self.assertFalse((self.root / "node").exists())

    def test_child_output_is_suppressed_and_address_output_env_removed(self):
        with patch.object(deploy.subprocess, "Popen") as popen:
            popen.return_value.wait.return_value = 0
            popen.return_value.poll.return_value = 0
            self.assertEqual(deploy.run_tailcat(["tailcat"], self.env, "forward"), 0)
        kwargs = popen.call_args.kwargs
        self.assertNotIn("TAILCAT_ADDR_FILE", kwargs["env"])
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)

    def start_tls_relay(self, san="127.0.0.1"):
        cert, key = self.certificate(san)
        store = RelayStore(self.root / "relay.db")
        self.addCleanup(store.close)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store, self.token.read_text()))
        enable_tls(server, cert, key)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.addCleanup(cleanup)
        return server

    def test_probe_through_tcp_forward_retains_tls_and_authentication(self):
        relay = self.start_tls_relay()
        # A local byte-forwarder checks TLS-over-TCP semantics, not Tailcat NAT.
        class Forward(socketserver.BaseRequestHandler):
            def handle(inner):
                with socket.create_connection(relay.server_address, timeout=5) as upstream:
                    peers = (inner.request, upstream)
                    while True:
                        readable, _, _ = select.select(peers, [], [], 5)
                        if not readable:
                            return
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            (upstream if source is inner.request else inner.request).sendall(data)
        forward = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Forward)
        thread = threading.Thread(target=forward.serve_forever, daemon=True)
        thread.start()
        try:
            env = dict(self.env, TAILCAT_LOCAL_PORT=str(forward.server_address[1]))
            self.assertTrue(deploy.probe(env)["https_verified"])
            self.token.write_text("wrong-token")
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(deploy.main(["probe"]), 1)
            self.assertNotIn("wrong-token", output.getvalue())
        finally:
            forward.shutdown()
            forward.server_close()
            thread.join(timeout=2)

    def test_probe_rejects_wrong_certificate_name(self):
        relay = self.start_tls_relay("127.0.0.2")
        with self.assertRaises(Exception) as result:
            deploy.probe(dict(self.env, TAILCAT_LOCAL_PORT=str(relay.server_port)))
        self.assertIn("CERTIFICATE_VERIFY_FAILED", str(result.exception))


if __name__ == "__main__":
    unittest.main()

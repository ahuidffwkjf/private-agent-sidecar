import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

from agent_sidecar.cli import main
from agent_sidecar.capabilities import collect_capabilities
from agent_sidecar.preflight import run_preflight
from agent_sidecar.service import Sidecar, make_handler
from agent_sidecar.store import LocalStore


class OptionalFLTests(unittest.TestCase):
    def test_base_imports_do_not_load_training(self):
        result = subprocess.run([sys.executable, "-c", "import sys; import agent_sidecar.cli; import agent_sidecar.service; assert not any(n.startswith('sidecar_fl') for n in sys.modules)"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_base_cli_has_no_training_commands(self):
        output = io.StringIO()
        with patch("agent_sidecar.cli.fl_available", return_value=False), patch.object(sys, "argv", ["agent-sidecar", "--help"]), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 0)
        self.assertNotIn("federated", output.getvalue())
        self.assertNotIn("benchmark", output.getvalue())
        self.assertIn("serve", output.getvalue())

    def test_core_does_not_create_or_delete_fl_tables(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.db"
            store = LocalStore(path)
            names = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertFalse(any(name.startswith("fl_") for name in names))
            with store.db:
                store.db.execute("CREATE TABLE fl_legacy (value TEXT)")
                store.db.execute("INSERT INTO fl_legacy VALUES ('keep')")
            store.db.close()
            reopened = LocalStore(path)
            self.assertEqual(reopened.db.execute("SELECT value FROM fl_legacy").fetchone()[0], "keep")
            reopened.db.close()

    def test_capability_does_not_advertise_missing_addon(self):
        with tempfile.TemporaryDirectory() as temporary, patch("agent_sidecar.capabilities.fl_available", return_value=False), patch("agent_sidecar.capabilities.detect_hardware", return_value={}), patch("agent_sidecar.capabilities.probe_relay", return_value={}):
            value = collect_capabilities("node", Path(temporary), "http://127.0.0.1:1")
            self.assertFalse(value["services"]["federated_learning"])
            self.assertTrue(value["services"]["encrypted_messages"])

    def test_missing_addon_fails_explicit_training_preflight(self):
        with tempfile.TemporaryDirectory() as temporary, patch("agent_sidecar.optional_fl.available", return_value=False):
            path = Path(temporary) / "harness.json"
            path.write_text('{"kind":"toy"}')
            report = run_preflight(harness_config=path)
            self.assertEqual(report["status"], "needs_configuration")
            self.assertIn("optional FL package", report["harness_error"])
            self.assertEqual(run_preflight()["status"], "ready")

    def test_base_http_surfaces_without_addon(self):
        with tempfile.TemporaryDirectory() as temporary, patch("agent_sidecar.service.fl_available", return_value=False), patch("agent_sidecar.optional_fl.available", return_value=False):
            # No Relay or existing node data is required for these local routes.
            sidecar = Sidecar.__new__(Sidecar)
            sidecar.store = LocalStore(Path(temporary) / "state.db")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(sidecar))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                for route in ("/v1/federated/rounds", "/v1/federated/recoveries"):
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(base + route)
                    self.assertEqual(raised.exception.code, 404)
                    raised.exception.close()
                with urlopen(base + "/ui/admin") as response:
                    html = response.read().decode()
                    self.assertIn('id="training_panel" class="card" style="margin-top:16px" hidden', html)
                    self.assertIn("const trainingEnabled=false", html)
                    self.assertNotIn("__TRAINING_", html)
                with self.assertRaisesRegex(ValueError, "optional FL package"):
                    sidecar.test_harness_config({"kind": "toy"})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                sidecar.store.db.close()

    def test_installer_allowlist_excludes_addon(self):
        from scripts.sidecar_setup import source_files
        root = Path(__file__).resolve().parents[1]
        files = [str(path.relative_to(root)) for path in source_files(root)]
        self.assertFalse(any("federated/" in name or "harness.py" in name for name in files))
        self.assertIn("agent_sidecar/service.py", files)

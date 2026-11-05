import os
import json
import sqlite3
import subprocess
import base64
import tempfile
import unittest
from pathlib import Path

from agent_sidecar.key_store import ExternalCommandProjectKeyBackend, FileProjectKeyBackend, KeychainProjectKeyBackend
from agent_sidecar.store import LocalStore
from agent_sidecar.preflight import run_preflight


class KeyStoreTests(unittest.TestCase):
    def test_file_backend_round_trip_is_0600_and_local_store_does_not_keep_key_material_in_sqlite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backend = FileProjectKeyBackend(root / "project-keys.json")
            store = LocalStore(root / "state.db", backend)
            store.create_project("project-key", "Key Project", "agent:owner")
            store.save_project_content_key("project-key", 1, "secret-base64-key", "sha256:fingerprint")
            record = store.project_content_key("project-key", 1)
            self.assertEqual(record["key_b64"], "secret-base64-key")
            self.assertEqual(record["key_fingerprint"], "sha256:fingerprint")
            self.assertEqual((root / "project-keys.json").stat().st_mode & 0o777, 0o600)
            db = sqlite3.connect(root / "state.db")
            try:
                self.assertEqual(db.execute("SELECT key_b64 FROM project_content_keys").fetchone()[0], "")
            finally:
                db.close()
            store.close()

    def test_existing_sqlite_key_material_migrates_to_file_backend(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlite_store = LocalStore(root / "state.db")
            sqlite_store.create_project("project-migrate", "Migrate", "agent:owner")
            sqlite_store.save_project_content_key("project-migrate", 2, "old-key", "sha256:old")
            sqlite_store.close()
            store = LocalStore(root / "state.db", FileProjectKeyBackend(root / "project-keys.json"))
            self.assertEqual(store.project_content_key("project-migrate", 2)["key_b64"], "old-key")
            db = sqlite3.connect(root / "state.db")
            try:
                self.assertEqual(db.execute("SELECT key_b64 FROM project_content_keys").fetchone()[0], "")
            finally:
                db.close()
            store.close()

    def test_keychain_backend_uses_security_commands_without_printing_secret(self):
        values = {}
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            if "add-generic-password" in args:
                values[(args[args.index("-a") + 1], args[args.index("-s") + 1])] = args[args.index("-w") + 1]
                return subprocess.CompletedProcess(args, 0, "", "")
            account = args[args.index("-a") + 1]
            service = args[args.index("-s") + 1]
            if "find-generic-password" in args:
                value = values.get((account, service))
                return subprocess.CompletedProcess(args, 0 if value else 44, (value or "") + "\n", "")
            values.pop((account, service), None)
            return subprocess.CompletedProcess(args, 0, "", "")

        backend = KeychainProjectKeyBackend(command="fake-security", runner=runner)
        key_material = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
        backend.save("project", 3, key_material, "sha256:key")
        record = backend.get("project", 3)
        self.assertEqual(record["key_b64"], key_material)
        self.assertEqual(record["key_fingerprint"], "sha256:" + __import__("hashlib").sha256(b"k" * 32).hexdigest())
        backend.delete("project", 3)
        self.assertIsNone(backend.get("project", 3))
        self.assertTrue(all(key_material not in str(call[0]) for call in calls if "find-generic-password" in call[0] or "delete-generic-password" in call[0]))

    def test_external_backend_sends_secrets_only_on_stdin_and_supports_round_trip(self):
        values = {}
        requests = []

        def runner(args, **kwargs):
            payload = json.loads(kwargs["input"])
            requests.append((list(args), payload))
            slot = (payload.get("project_id"), payload.get("epoch"))
            if payload["operation"] == "save":
                values[slot] = {"project_id": slot[0], "epoch": slot[1], "key_b64": payload["key_b64"], "key_fingerprint": payload["key_fingerprint"]}
                response = {"ok": True}
            elif payload["operation"] == "get":
                response = {"ok": True, "record": values.get(slot)}
            else:
                values.pop(slot, None)
                response = {"ok": True}
            return subprocess.CompletedProcess(args, 0, json.dumps(response), "")

        backend = ExternalCommandProjectKeyBackend(["/usr/local/bin/hsm-helper"], runner=runner)
        backend.save("project", 4, "secret-key", "sha256:fingerprint")
        self.assertEqual(backend.get("project", 4)["key_b64"], "secret-key")
        backend.delete("project", 4)
        self.assertIsNone(backend.get("project", 4))
        self.assertTrue(all("secret-key" not in args for args, _ in requests))
        self.assertEqual(requests[0][0], ["/usr/local/bin/hsm-helper"])

    def test_external_backend_fails_closed_on_helper_error_or_scope_mismatch(self):
        def failing_runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 17, "", "device unavailable")

        backend = ExternalCommandProjectKeyBackend(["hsm-helper"], runner=failing_runner)
        with self.assertRaisesRegex(RuntimeError, "helper failed"):
            backend.get("project", 1)

        def mismatched_runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, json.dumps({"ok": True, "record": {"project_id": "other", "epoch": 1, "key_b64": "k", "key_fingerprint": "f"}}), "")

        backend = ExternalCommandProjectKeyBackend(["hsm-helper"], runner=mismatched_runner)
        with self.assertRaisesRegex(RuntimeError, "scope-mismatched"):
            backend.get("project", 1)

    def test_preflight_fails_closed_for_external_backend_without_helper(self):
        report = run_preflight(project_key_backend="external", project_key_command=["/definitely/missing/helper"])
        self.assertEqual(report["project_key_backend"], "external")
        self.assertFalse(report["project_key_backend_available"])
        self.assertEqual(report["status"], "needs_configuration")

    def test_preflight_reports_keychain_availability_without_secrets(self):
        report = run_preflight(project_key_backend="keychain")
        self.assertEqual(report["project_key_backend"], "keychain")
        self.assertIn("project_key_backend_available", report)
        encoded = json.dumps(report).lower()
        self.assertNotIn("key_b64", encoded)
        self.assertNotIn("secret", encoded)

    def test_production_preflight_requires_https_direct_endpoint_and_non_sqlite_backend(self):
        report = run_preflight(
            relay_url="http://relay.example:8787",
            direct_endpoint="http://node.example:8801",
            project_key_backend="sqlite",
            production=True,
        )
        self.assertEqual(report["status"], "needs_configuration")
        self.assertFalse(report["relay_url_https"])
        self.assertFalse(report["direct_endpoint_https"])
        self.assertFalse(report["production_key_backend_non_sqlite"])


if __name__ == "__main__":
    unittest.main()

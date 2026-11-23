import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import package_toy_release as packer


class ToyReleaseTests(unittest.TestCase):
    def test_bundle_contains_runnable_toy_and_valid_checksums(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "toy.zip"
            result = packer.package(output)
            self.assertEqual(result["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            with zipfile.ZipFile(output) as archive:
                names = {name.removeprefix(packer.PREFIX) for name in archive.namelist()}
                for line in archive.read(packer.PREFIX + "SHA256SUMS").decode().splitlines():
                    digest, name = line.split("  ", 1)
                    self.assertEqual(hashlib.sha256(archive.read(packer.PREFIX + name)).hexdigest(), digest)
                required = {"README.md", "docs/TOY_USER_GUIDE.md", "scripts/toy_workspace.py", "scripts/run_toy_process_demo.py",
                            "scripts/research_admin.py", "agent_sidecar/web/toy.js", "agent_sidecar/toy_tasks.py",
                            "agent_sidecar/research.py", "plugins/dsh-private-agent-sidecar/lib/index.js", "tests/test_toy_release.py"}
                self.assertTrue(required.issubset(names))
                manifest = json.loads(archive.read(packer.PREFIX + "MANIFEST.json"))
                self.assertFalse(manifest["includes_credentials"])
                self.assertFalse(manifest["includes_federated_training"])
                self.assertEqual(archive.read(packer.PREFIX + "README.md"), archive.read(packer.PREFIX + "docs/TOY_USER_GUIDE.md"))
                self.assertTrue(all(packer.allowed_path(name) for name in names))
            with self.assertRaises(ValueError):
                packer.package(output)

    def test_private_paths_and_sensitive_material_are_rejected(self):
        for name in ("runtime/key", "runs/research.db", "output/report.pdf", ".git/config", "private-exports/a.jsonl",
                     "agent_sidecar/identity.json", "deploy/server.env", "foo.key", "foo.private.json", "packages/federated/a.py", "../escape"):
            self.assertFalse(packer.allowed_path(name), name)
        with patch.object(packer, "release_files", return_value=[ROOT / "docs/TOY_USER_GUIDE.md"]), patch.object(Path, "read_bytes", return_value=b"-----BEGIN OPENSSH PRIVATE KEY-----\nsecret"):
            with self.assertRaisesRegex(ValueError, "credential-like"):
                packer.source_content()

    def test_source_change_detected_before_creating_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "toy.zip"
            with patch.object(packer, "source_content", side_effect=[{"README.md": b"one"}, {"README.md": b"two"}]):
                with self.assertRaisesRegex(ValueError, "source changed"):
                    packer.package(output)
            self.assertFalse(output.exists())

    def test_ancestor_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "external"
            external.mkdir()
            source = external / "script.py"
            source.write_text("print('not packaged')\n")
            (root / "agent_sidecar").symlink_to(external, target_is_directory=True)
            with patch.object(packer, "installer_files", return_value=[root / "agent_sidecar/script.py"]):
                with self.assertRaises(ValueError):
                    packer.release_files(root)

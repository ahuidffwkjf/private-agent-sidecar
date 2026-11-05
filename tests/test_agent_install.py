import hashlib
import importlib.util
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import sidecar_setup as setup
import export_agent_skills as exporter
import package_sidecar_installer as packer


class AgentInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'

    def test_unknown_home_not_overwritten(self):
        self.home.mkdir()
        (self.home / 'important').write_text('keep')
        with self.assertRaises(setup.SetupError):
            with setup.home_lock(self.home):
                pass
        self.assertEqual((self.home / 'important').read_text(), 'keep')

    def test_home_lock_is_exclusive_and_private(self):
        with setup.home_lock(self.home):
            self.assertEqual(self.home.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(setup.SetupError):
                with setup.home_lock(self.home):
                    pass

    def test_symlink_home_refused(self):
        target = self.root / 'target'
        target.mkdir()
        self.home.symlink_to(target)
        with self.assertRaises(setup.SetupError):
            with setup.home_lock(self.home):
                pass
        self.assertEqual(list(target.iterdir()), [])

    def test_export_has_361_real_skill_entrypoints_and_original_contracts(self):
        index = exporter.export_skills(self.root / 'skills')
        self.assertEqual(len(index), 361)
        self.assertEqual(len({s['name'] for s in index}), 361)
        for item in index:
            path = self.root / 'skills' / item['path']
            self.assertTrue(path.exists())
            self.assertTrue(path.read_text().startswith('---\nname: '))
            self.assertIn('not_implemented', path.read_text())
            manifest = json.loads((path.parent / 'references/manifest.json').read_text())
            self.assertEqual(manifest['skill_id'], item['skill_id'])
            self.assertEqual(manifest['execution'], 'disabled-by-default')

    def test_skill_conflict_preflight_writes_nothing(self):
        source, target = self.root / 'source', self.root / 'target'
        exporter.export_skills(source)
        self.home.mkdir(mode=0o700)
        setup.write_json(self.home / 'installed.json', {'schema': setup.SCHEMA, 'skills': str(source)})
        conflict = target / 'wechat-message-text'
        conflict.mkdir(parents=True)
        (conflict / 'SKILL.md').write_text('user content')
        with self.assertRaises(setup.SetupError):
            setup.install_skills(self.home, target)
        self.assertEqual(list(target.iterdir()), [conflict])
        self.assertEqual((conflict / 'SKILL.md').read_text(), 'user content')

    def test_bundle_checksum_and_source_allowlist(self):
        output = self.root / 'installer.zip'
        result = packer.package(output)
        self.assertEqual(result['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            prefix = 'sidecar-agent-installer/'
            for line in archive.read(prefix + 'SHA256SUMS').decode().splitlines():
                digest, name = line.split('  ', 1)
                self.assertEqual(hashlib.sha256(archive.read(prefix + name)).hexdigest(), digest)
            self.assertEqual(sum(name.endswith('/SKILL.md') for name in names), 362)
            self.assertFalse(any('/runtime/' in n or '/.git/' in n or n.endswith('.db') for n in names))
        with self.assertRaises(ValueError):
            packer.package(output)

    def test_access_bundle_wrong_hash_and_wrong_device_rejected(self):
        self.home.mkdir(mode=0o700)
        setup.write_json(self.home / 'installed.json', {'schema': setup.SCHEMA})
        enrollment = {'tailcat_client_public_key': 'nodekey:' + 'a' * 64, 'source_sha256': 'b' * 64}
        setup.write_json(self.home / 'enrollment.json', enrollment)
        bundle = self.root / 'access.json'
        setup.write_json(bundle, {'schema': 'sidecar-access/1', 'tailcat_client_public_key': 'nodekey:' + 'c' * 64})
        with self.assertRaises(setup.SetupError):
            setup.configure(self.home, bundle, '0' * 64)
        with self.assertRaisesRegex(setup.SetupError, 'another device'):
            setup.configure(self.home, bundle, hashlib.sha256(bundle.read_bytes()).hexdigest())
        self.assertFalse((self.home / 'access.json').exists())

    def test_configuration_never_claims_connected(self):
        self.home.mkdir(mode=0o700)
        setup.write_json(self.home / 'installed.json', {'schema': setup.SCHEMA})
        enrollment = {'tailcat_client_public_key': 'nodekey:' + 'a' * 64, 'source_sha256': 'b' * 64}
        setup.write_json(self.home / 'enrollment.json', enrollment)
        bundle = self.root / 'access.json'
        setup.write_json(bundle, dict(enrollment, schema='sidecar-access/1',
                        tailcat_address='tc' + 'x' * 100, relay_token='secret', relay_ca_pem='test-ca'))
        with patch.object(setup.ssl, 'create_default_context'):
            result = setup.configure(self.home, bundle, hashlib.sha256(bundle.read_bytes()).hexdigest())
        self.assertEqual(result['status'], 'configured')
        self.assertEqual((self.home / 'access.json').stat().st_mode & 0o777, 0o600)

    def test_readiness_fails_on_child_exit(self):
        from unittest.mock import Mock
        child = Mock()
        child.poll.return_value = 1
        with self.assertRaisesRegex(setup.SetupError, 'exited'):
            setup.wait_ready(lambda: True, [child])

    def test_documentation_skill_copy_is_current(self):
        self.assertEqual((ROOT / 'docs/AGENT_INSTALL.md').read_bytes(),
                         (ROOT / 'skills/sidecar-onboarding/references/install.md').read_bytes())

    def test_incomplete_source_is_not_packaged(self):
        with self.assertRaisesRegex(setup.SetupError, 'incomplete'):
            setup.source_files(self.root)

    def test_bad_ca_and_colliding_ports_are_rejected(self):
        enrollment = {'tailcat_client_public_key': 'nodekey:' + 'a' * 64, 'source_sha256': 'b' * 64}
        value = dict(enrollment, schema='sidecar-access/1', tailcat_address='tc' + 'x' * 100,
                     relay_token='secret', relay_ca_pem=None)
        with self.assertRaisesRegex(setup.SetupError, 'PEM CA'):
            setup.validate_access(value, enrollment)
        with self.assertRaisesRegex(setup.SetupError, 'must differ'):
            setup.validate_access(dict(value, local_port=8802, node_port=8802), enrollment)


if __name__ == '__main__':
    unittest.main()

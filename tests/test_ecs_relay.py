import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ecs_relay', Path(__file__).parents[1] / 'scripts/ecs_relay.py')
ecs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ecs)


class EcsRelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.token = self.root / 'token'
        self.token.write_text('x' * 48)
        self.token.chmod(0o600)
        self.key = self.root / 'key'
        self.key.write_text('test fixture')
        self.key.chmod(0o600)
        self.config = {'host': '127.0.0.1', 'port': 8787, 'database': str(self.root / 'relay.db'),
                       'tls_cert': str(self.root / 'cert'), 'tls_key': str(self.key),
                       'token_file': str(self.token), 'rate_limit_per_minute': 1000}
        self.path = self.root / 'config.json'

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        self.path.write_text(json.dumps(self.config))
        return ecs.load_config(self.path)

    def test_requires_tls_pair_and_does_not_return_token_in_configuration(self):
        with patch.object(ecs.ssl, 'SSLContext') as factory:
            value, token, context = self.load()
            self.assertEqual(token, 'x' * 48)
            self.assertNotIn(token, json.dumps(value))
            self.assertEqual(context.minimum_version, ecs.ssl.TLSVersion.TLSv1_2)
            context.load_cert_chain.assert_called_once()

    def test_missing_tls_or_empty_token_fails_closed(self):
        del self.config['tls_cert']
        with self.assertRaises(ValueError):
            self.load()
        self.config['tls_cert'] = str(self.root / 'cert')
        self.token.write_text('')
        with self.assertRaises(ValueError):
            self.load()

    @unittest.skipIf(os.name == 'nt', 'POSIX deployment permissions')
    def test_rejects_world_readable_token_and_symlink(self):
        self.token.chmod(0o644)
        with self.assertRaises(ValueError):
            self.load()
        self.token.chmod(0o600)
        link = self.root / 'link'
        link.symlink_to(self.token)
        self.config['token_file'] = str(link)
        with self.assertRaises(ValueError):
            self.load()

    def test_rejects_unbounded_rate_and_relative_database(self):
        self.config['rate_limit_per_minute'] = 0
        with self.assertRaises(ValueError):
            self.load()
        self.config['rate_limit_per_minute'] = 1000
        self.config['database'] = 'relative.db'
        with self.assertRaises(ValueError):
            self.load()

import json
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts.ecs_toy_fixture import Fixture
from agent_sidecar.online_desktop import OnlineDesktopRuntime, load_access


class OnlineDesktopTests(unittest.TestCase):
    def test_single_user_tls_runtime_restores_identity_and_requires_consent(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            root = Path(directory)
            fixture = Fixture(root)
            runtime = None
            try:
                session = secrets.token_hex(32)
                runtime = OnlineDesktopRuntime(root / 'user', session, fixture.access_file)
                origin = runtime.start()
                identity = runtime.sidecar.identity.agent_id
                with self.assertRaises(HTTPError) as caught:
                    urlopen(origin + '/v1/identity')
                self.assertEqual(caught.exception.code, 401)
                caught.exception.close()
                headers = {'Cookie': 'sidecar_desktop_session=' + session}
                with urlopen(Request(origin + '/v1/online/status', headers=headers)) as response:
                    self.assertEqual(json.load(response)['mode'], 'ecs')
                self.assertFalse(runtime.sidecar.toy.research.status()['consent_active'])
                runtime.close()
                runtime = OnlineDesktopRuntime(root / 'user', secrets.token_hex(32), fixture.access_file)
                runtime.start()
                self.assertEqual(runtime.sidecar.identity.agent_id, identity)
                self.assertEqual(runtime.sidecar.direct_endpoint, '')
            finally:
                if runtime:
                    runtime.close()
                fixture.close()

    def test_access_file_rejects_plaintext_and_embedded_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)
            try:
                value = json.loads(fixture.access_file.read_text())
                for url in ['http://127.0.0.1:8787', 'https://user:pass@example.com:8787', 'https://example.com:8787/?secret=bad']:
                    value['relay_url'] = url
                    fixture.access_file.write_text(json.dumps(value))
                    with self.assertRaises(ValueError):
                        load_access(fixture.access_file)
            finally:
                fixture.close()

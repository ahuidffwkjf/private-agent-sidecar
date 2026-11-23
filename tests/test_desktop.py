import json
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent_sidecar.desktop import COOKIE, DesktopRuntime


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.token = secrets.token_hex(32)
        self.runtime = DesktopRuntime(Path(self.temp.name), self.token)
        self.url = self.runtime.start()

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def request(self, path, value=None, headers=None, authenticated=True):
        fields = {'Cookie': f'{COOKIE}={self.token}'} if authenticated else {}
        if value is not None:
            fields['Content-Type'] = 'application/json'
        fields.update(headers or {})
        request = Request(self.url + path, data=json.dumps(value).encode() if value is not None else None, headers=fields)
        return urlopen(request, timeout=5)

    def test_session_required_for_ui_api_and_mutation(self):
        for path, data in [('/ui', None), ('/v1/identity', None), ('/v1/projects', {'name': 'no'})]:
            with self.assertRaises(HTTPError) as caught:
                self.request(path, data, authenticated=False)
            self.assertEqual(caught.exception.code, 401)
        with self.request('/ui?desktop=1') as response:
            self.assertIn('Sidecar · 聊天', response.read().decode())
        with self.request('/v1/identity') as response:
            self.assertTrue(json.load(response)['agent_id'].startswith('agent:'))

    def test_cross_origin_and_dns_rebinding_rejected_even_with_cookie(self):
        for headers in [{'Origin': 'https://evil.example'}, {'Host': 'evil.example'}, {'Sec-Fetch-Site': 'cross-site'}]:
            with self.assertRaises(HTTPError) as caught:
                self.request('/v1/projects', {'name': 'no'}, headers)
            self.assertEqual(caught.exception.code, 403)
        with self.request('/v1/projects', {'name': 'desktop test'}, {'Origin': self.url}) as response:
            self.assertEqual(json.load(response)['name'], 'desktop test')

    def test_signed_direct_endpoint_still_fails_closed(self):
        with self.request('/v1/direct/healthz', authenticated=False) as response:
            self.assertEqual(json.load(response)['status'], 'ok')
        with self.assertRaises(HTTPError) as caught:
            self.request('/v1/direct/messages', {'scope': 'private', 'envelope': {}}, authenticated=False)
        self.assertEqual(caught.exception.code, 400)

    def test_restart_keeps_identity_and_messages_but_rotates_session(self):
        with self.request('/v1/identity') as response:
            identity = json.load(response)['agent_id']
        with self.request('/v1/messages/send', {'target': {'kind': 'public', 'channel': 'public'}, 'message': {'payload': {'text': 'persistent desktop test'}}}):
            pass
        self.runtime.close()
        old_token = self.token
        self.token = secrets.token_hex(32)
        self.runtime = DesktopRuntime(Path(self.temp.name), self.token)
        self.url = self.runtime.start()
        with self.request('/v1/identity') as response:
            self.assertEqual(json.load(response)['agent_id'], identity)
        with self.request('/v1/broadcasts') as response:
            self.assertTrue(any(x.get('content') == {'text': 'persistent desktop test'} for x in json.load(response)['broadcasts']))
        with self.assertRaises(HTTPError) as caught:
            self.request('/ui', headers={'Cookie': f'{COOKIE}={old_token}'})
        self.assertEqual(caught.exception.code, 401)

    def test_close_releases_owned_listeners(self):
        self.runtime.close()
        with self.assertRaises(URLError):
            self.request('/ui')


class DesktopProcessTests(unittest.TestCase):
    def test_stdin_eof_stops_backend_and_readiness_never_contains_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            token = secrets.token_hex(32)
            data_dir = str(Path(temporary) / '桌面工作区')
            entry = 'import faulthandler; faulthandler.dump_traceback_later(10, repeat=True); from agent_sidecar.desktop import main; main()'
            process = subprocess.Popen([sys.executable, '-c', entry], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', env={**os.environ, 'PYTHONIOENCODING': 'ascii'})
            try:
                try:
                    stdout, stderr = process.communicate(json.dumps({'token': token, 'data_dir': data_dir}, ensure_ascii=False) + '\n', timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                    self.fail('Desktop startup exceeded 30 seconds:\n' + stderr.replace(token, '[redacted]'))
                self.assertEqual(process.returncode, 0, stderr)
                self.assertNotIn(token, stdout + stderr)
                self.assertEqual(len(stdout.strip().splitlines()), 1)
                self.assertTrue(json.loads(stdout)['url'].startswith('http://127.0.0.1:'))
                self.assertTrue((Path(data_dir) / 'node/identity.json').exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()

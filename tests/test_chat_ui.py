import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

from agent_sidecar.service import make_handler


class ChatUITests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(SimpleNamespace()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_home_and_ui_serve_chat_shell(self):
        for path in ('/', '/ui'):
            with urlopen(self.url + path) as response:
                html = response.read().decode()
                self.assertIn('Sidecar · 聊天', html)
                self.assertIn('aria-label="主导航"', html)
                self.assertIn('id="composer"', html)
                self.assertIn('/ui/app.js', html)
                self.assertNotIn('const HARNESS_TEMPLATES', html)

    def test_old_dashboard_remains_available(self):
        with urlopen(self.url + '/ui/admin') as response:
            html = response.read().decode()
            self.assertIn('新节点 Harness 配置', html)
            self.assertIn('节点通信 Skill 目录', html)

    def test_assets_have_explicit_mime_types(self):
        for asset, content_type in (('app.css', 'text/css'), ('app.js', 'text/javascript'), ('lucide.js', 'text/javascript')):
            with urlopen(self.url + '/ui/' + asset) as response:
                self.assertEqual(response.headers.get_content_type(), content_type)
                self.assertGreater(len(response.read()), 100)

    def test_asset_route_does_not_expose_arbitrary_files(self):
        for path in ('/ui/../service.py', '/ui/%2e%2e/service.py', '/ui/no-such-asset.js'):
            with self.assertRaises(HTTPError) as caught:
                urlopen(self.url + path)
            self.assertEqual(caught.exception.code, 404)


if __name__ == '__main__':
    unittest.main()

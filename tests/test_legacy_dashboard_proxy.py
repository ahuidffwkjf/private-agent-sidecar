import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("legacy_dashboard_proxy", ROOT / "scripts" / "legacy_dashboard_proxy.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FakeSidecarHandler(BaseHTTPRequestHandler):
    seen_authorization = ""
    seen_body = None

    def do_GET(self):
        type(self).seen_authorization = self.headers.get("Authorization", "")
        payload = {"agent_id": "agent:test"} if self.path == "/v1/identity" else {"status": "ok"}
        data = json.dumps(payload).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        type(self).seen_authorization = self.headers.get("Authorization", "")
        type(self).seen_body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        data = json.dumps({"trusted": True}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def log_message(self, format, *args):
        return


class LegacyDashboardProxyTests(unittest.TestCase):
    def test_dashboard_serves_ui_and_injects_bearer_token(self):
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeSidecarHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True); upstream_thread.start()
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), MODULE.make_handler(f"http://127.0.0.1:{upstream.server_port}", "secret-token"))
        proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True); proxy_thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{proxy.server_port}/") as response:
                self.assertIn("Sidecar 0.3 可视化控制台", response.read().decode())
            with urlopen(f"http://127.0.0.1:{proxy.server_port}/api/v1/identity") as response:
                self.assertEqual(json.loads(response.read())["agent_id"], "agent:test")
            self.assertEqual(FakeSidecarHandler.seen_authorization, "Bearer secret-token")
            request = Request(
                f"http://127.0.0.1:{proxy.server_port}/api/v1/peers/trust",
                data=json.dumps({"agent_id": "agent:peer", "expected_signing_key": "key"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                self.assertTrue(json.loads(response.read())["trusted"])
            self.assertEqual(FakeSidecarHandler.seen_body["agent_id"], "agent:peer")
        finally:
            proxy.shutdown(); proxy.server_close(); proxy_thread.join(timeout=2)
            upstream.shutdown(); upstream.server_close(); upstream_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

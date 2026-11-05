import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent_sidecar.relay import make_handler
from agent_sidecar.service import DASHBOARD_UI, TRUST_UI, Sidecar
from agent_sidecar.store import RelayStore


class HarnessConfigUITests(unittest.TestCase):
    def test_harness_config_is_validated_persisted_and_redacted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(RelayStore(root / "relay.db")))
            thread = threading.Thread(target=relay_server.serve_forever, daemon=True)
            thread.start()
            try:
                sidecar = Sidecar(root / "node", f"http://127.0.0.1:{relay_server.server_port}")
                self.assertIn("新节点 Harness 配置", DASHBOARD_UI)
                self.assertIn("/v1/harness/config", DASHBOARD_UI)
                self.assertIn("分布式项目留言板", DASHBOARD_UI)
                self.assertIn("/v1/projects/${encodeURIComponent(id)}/board", DASHBOARD_UI)
                self.assertIn("/v1/peers/trust-notifications", DASHBOARD_UI)
                self.assertIn("/v1/peers/trust-requests", TRUST_UI)
                self.assertIn("接受并反向信任", TRUST_UI)
                self.assertIn("/v1/project-invitations", DASHBOARD_UI)
                self.assertIn("接受并加入", DASHBOARD_UI)
                checked = sidecar.test_harness_config({"kind": "toy", "seed": 7, "samples": 10, "node_index": 0})
                self.assertTrue(checked["ok"])
                self.assertEqual(checked["descriptor"]["name"], "toy")

                configured = sidecar.configure_harness({
                    "kind": "dsh-controller",
                    "controller_url": "http://127.0.0.1:8081",
                    "registry_token": "secret-token",
                })
                self.assertTrue(configured["configured"])
                self.assertEqual(configured["config"]["registry_token"], "********")
                self.assertEqual(sidecar.capabilities["harness"]["name"], "deepseek-controller")

                saved = json.loads((root / "node" / "harness-config.json").read_text("utf-8"))
                self.assertEqual(saved["registry_token"], "secret-token")
                self.assertEqual(sidecar.harness_config()["config"]["registry_token"], "********")

                sidecar.configure_harness({
                    "kind": "dsh-controller",
                    "controller_url": "http://127.0.0.1:8081",
                    "registry_token": "********",
                })
                saved_again = json.loads((root / "node" / "harness-config.json").read_text("utf-8"))
                self.assertEqual(saved_again["registry_token"], "secret-token")
            finally:
                relay_server.shutdown()
                relay_server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

import tempfile
import time
import unittest
from pathlib import Path

from agent_sidecar.store import LocalStore


class GossipMetricTests(unittest.TestCase):
    def test_success_and_failure_metrics_persist_across_store_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.db"
            store = LocalStore(path)
            store.create_project("project-metrics", "Metrics", "agent:owner")
            store.record_project_gossip_result("project-metrics", "agent:peer", True, now=100)
            store.record_project_gossip_result("project-metrics", "agent:peer", False, now=110)
            metric = store.project_gossip_peer_metric("project-metrics", "agent:peer")
            self.assertEqual(metric["success_score"], 1.0)
            self.assertEqual(metric["failure_score"], 1.0)
            store.close()
            reopened = LocalStore(path)
            try:
                persisted = reopened.project_gossip_peer_metric("project-metrics", "agent:peer")
                self.assertEqual(persisted["last_success_at"], 100)
                self.assertEqual(persisted["last_failure_at"], 110)
            finally:
                reopened.close()

    def test_metric_updates_are_monotonic(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = LocalStore(Path(temporary) / "state.db")
            store.record_project_gossip_result("project", "peer", True, now=100)
            store.record_project_gossip_result("project", "peer", True, now=101)
            store.record_project_gossip_result("project", "peer", False, now=102)
            metric = store.project_gossip_peer_metric("project", "peer")
            self.assertEqual(metric["success_score"], 2.0)
            self.assertEqual(metric["failure_score"], 1.0)

    def test_attempt_history_is_bounded_and_keeps_recent_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = LocalStore(Path(temporary) / "state.db")
            for index in range(125):
                store.record_project_gossip_result("project", "peer", index % 2 == 0, now=100 + index)
            attempts = store.project_gossip_attempts("project", "peer", 100)
            self.assertEqual(len(attempts), 100)
            self.assertEqual(attempts[0]["recorded_at"], 224)
            self.assertEqual(attempts[-1]["recorded_at"], 125)


if __name__ == "__main__":
    unittest.main()

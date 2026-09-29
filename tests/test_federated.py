import json
import tempfile
import time
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen, Request

from agent_sidecar.federated import SimulationConfig, run_simulation
from agent_sidecar.federated.runtime import FederatedCoordinator, FederatedNode
from agent_sidecar.harness import ToyHarness
from agent_sidecar.federated.aggregators import get_aggregator
from agent_sidecar.federated.types import NodeUpdate, RoundConfig, EvaluateRequest, TrainRequest, model_hash, participant_set_hash
from agent_sidecar.harness import DshControllerAdapter, DshControllerTrainingAdapter, DshHeadlessAdapter, HttpHarnessAdapter, diagnose_dsh
from agent_sidecar.harness_factory import load_harness
from agent_sidecar.harness_server import make_handler as make_harness_handler
from agent_sidecar.preflight import run_preflight
from agent_sidecar.federated.artifacts import assemble_artifact, chunk_artifact
from agent_sidecar.federated.secure import mask_updates_for_secure_sum, secure_sum_equals_weighted_sum
from agent_sidecar.federated.privacy import gaussian_rdp_accountant
from agent_sidecar.relay import make_handler
from agent_sidecar.service import Sidecar, make_handler as make_sidecar_handler
from agent_sidecar.store import RelayStore


class _DshMockHandler:
    class Handler(__import__("http.server", fromlist=["BaseHTTPRequestHandler"]).BaseHTTPRequestHandler):
        jobs = {}
        def _json(self, value, status=200):
            data = json.dumps(value).encode("utf-8")
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_POST(self):
            if self.path != "/tasks": self._json({"error": "not found"}, 404); return
            length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length))
            self.__class__.jobs["job-1"] = {"status": "completed", "result": {"success": True, "output": payload["description"]}}
            self._json({"job_id": "job-1"}, 201)
        def do_GET(self):
            if self.path == "/tasks/job-1": self._json(self.__class__.jobs["job-1"]); return
            self._json({"error": "not found"}, 404)
        def log_message(self, *_args): return


class _DshTrainingMockHandler(_DshMockHandler.Handler):
    def do_POST(self):
        if self.path != "/tasks":
            return super().do_POST()
        length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length))
        operation = json.loads(payload["description"].split("{", 1)[1].rsplit("}", 1)[0].join(["{", "}"]))["operation"] if "{" in payload["description"] else "train"
        if operation == "evaluate":
            output = json.dumps({"metrics": {"accuracy": 0.75, "loss": 0.5}, "eval_ms": 4})
        else:
            output = json.dumps({"model": {"weights": [0.2, 0.3], "bias": [0.1]}, "num_examples": 8, "metrics": {"accuracy": 0.75}, "train_ms": 5})
        self.__class__.jobs["job-1"] = {"status": "completed", "result": {"success": True, "output": output}}
        self._json({"job_id": "job-1"}, 201)


class AggregatorTests(unittest.TestCase):
    def _updates(self):
        base = {"weights": [0.0, 0.0], "bias": [0.0]}
        updates = []
        for i, values in enumerate(([1.0, 1.0], [1.2, 0.8], [100.0, 100.0])):
            updates.append(NodeUpdate(f"u{i}", f"n{i}", "r1", {"weights": list(values), "bias": [float(i)]}, 10, 1, 1.0))
        return base, updates, RoundConfig("r1", "toy", "sha256:x", "trimmed_mean")

    def test_median_resists_extreme_update(self):
        base, updates, config = self._updates()
        result = get_aggregator("median").aggregate(base, updates, config)
        self.assertLess(result.model["weights"][0], 2.0)

    def test_trimmed_mean_removes_extreme_update(self):
        base, updates, config = self._updates()
        result = get_aggregator("trimmed_mean").aggregate(base, updates, config)
        self.assertLess(result.model["weights"][0], 2.0)

    def test_round_participant_set_commitment_rejects_tampering(self):
        expected = participant_set_hash(["node-a", "node-b"])
        valid = RoundConfig("r1", "toy", "sha256:x", "secure_fedavg", participants=("node-a", "node-b"), secure_aggregation=True, participant_set_hash=expected)
        self.assertEqual(valid.participant_set_hash, expected)
        with self.assertRaises(ValueError):
            RoundConfig("r1", "toy", "sha256:x", "secure_fedavg", participants=("node-a", "node-c"), secure_aggregation=True, participant_set_hash=expected)

    def test_update_hash_rejects_tampered_model(self):
        with self.assertRaises(ValueError):
            NodeUpdate("u", "n", "r", {"weights": [1.0], "bias": [0.0]}, 1, 1, 1.0, model_hash="sha256:base", update_hash="sha256:tampered")

    def test_gaussian_privacy_accountant_reports_epsilon(self):
        report = gaussian_rdp_accountant(noise_std=1.0, clip_norm=0.5, steps=10, delta=1e-5)
        self.assertIsNotNone(report.epsilon)
        self.assertEqual(report.delta, 1e-5)
        self.assertEqual(report.accountant, "basic_gaussian_rdp_upper_bound")

    def test_secure_masks_cancel_without_revealing_individual_update(self):
        updates = [
            NodeUpdate("u1", "node-1", "r1", {"weights": [1.0], "bias": [0.1]}, 2, 1, 1.0),
            NodeUpdate("u2", "node-2", "r1", {"weights": [3.0], "bias": [0.2]}, 3, 1, 1.0),
        ]
        masked = mask_updates_for_secure_sum(updates, "r1", seed=9)
        self.assertTrue(secure_sum_equals_weighted_sum(masked, updates))
        self.assertNotEqual(masked[0].model, {"weights": [2.0], "bias": [0.2]})
        secure = get_aggregator("secure_fedavg").aggregate({}, masked, RoundConfig("r1", "toy", "sha256:x", "secure_fedavg"))
        self.assertAlmostEqual(secure.model["weights"][0], (2 * 1.0 + 3 * 3.0) / 5, places=8)
        self.assertFalse(secure.robustness_stats["individual_updates_meaningful"])

    def test_krum_selects_one_update(self):
        base = {"weights": [0.0, 0.0], "bias": [0.0]}
        updates = [NodeUpdate(f"u{i}", f"n{i}", "r1", {"weights": [float(i) / 10, float(i) / 10], "bias": [0.0]}, 10, 1, 1.0) for i in range(5)]
        result = get_aggregator("krum").aggregate(base, updates, RoundConfig("r1", "toy", "sha256:x", "krum", max_byzantine=1))
        self.assertEqual(len(result.accepted_update_ids), 1)
        self.assertEqual(len(result.rejected_update_ids), 4)


class SimulationTests(unittest.TestCase):
    def test_label_flip_is_a_real_data_attack(self):
        result = run_simulation(SimulationConfig(nodes=5, rounds=3, malicious_nodes=1, attack="label_flip", strategy="fedavg"))
        self.assertEqual(result.security["attack"], "label_flip")
        self.assertGreater(result.security["malicious_influence_proxy"], 0.0)

    def test_secure_fedavg_simulation_reports_confidentiality_mode(self):
        result = run_simulation(SimulationConfig(nodes=4, rounds=2, strategy="secure_fedavg"))
        self.assertTrue(result.security["secure_aggregation"])
        self.assertFalse(result.security["individual_updates_meaningful"])

    def test_optional_privacy_noise_changes_model_deterministically(self):
        config = SimulationConfig(nodes=5, rounds=2, dp_clip_norm=0.5, dp_noise_std=0.05, seed=17)
        first = run_simulation(config)
        second = run_simulation(config)
        self.assertEqual(first.final_model, second.final_model)
        self.assertEqual(first.security["dp_noise_std"], 0.05)
        self.assertFalse(first.security["formal_privacy_guarantee"])
        self.assertEqual(first.security["privacy_accountant_status"], "upper_bound_under_declared_assumptions")
        self.assertIsNotNone(first.security["privacy_epsilon"])

    def test_topology_changes_network_metrics(self):
        star = run_simulation(SimulationConfig(nodes=8, rounds=2, topology="star"))
        hierarchical = run_simulation(SimulationConfig(nodes=8, rounds=2, topology="hierarchical"))
        gossip = run_simulation(SimulationConfig(nodes=8, rounds=2, topology="gossip"))
        self.assertEqual(star.config["topology"], "star")
        self.assertLess(hierarchical.communication["coordinator_messages"], star.communication["coordinator_messages"])
        self.assertEqual(gossip.communication["network_messages"], star.communication["network_messages"])
        self.assertGreater(gossip.latency["round_wall_time_ms_mean"], star.latency["round_wall_time_ms_mean"])

    def test_toy_federation_is_reproducible(self):
        config = SimulationConfig(nodes=4, rounds=4, seed=11)
        first = run_simulation(config)
        second = run_simulation(config)
        self.assertEqual(first.final_metrics, second.final_metrics)
        self.assertEqual([r["accuracy"] for r in first.rounds], [r["accuracy"] for r in second.rounds])

    def test_attack_is_reported(self):
        result = run_simulation(SimulationConfig(nodes=5, rounds=3, malicious_nodes=1, attack="scale", strategy="trimmed_mean"))
        self.assertEqual(result.security["attack"], "scale")
        self.assertGreaterEqual(result.security["malicious_update_accept_rate"], 0.0)
        self.assertIn("bytes_sent", result.communication)
        self.assertIn("malicious_influence_proxy", result.security)
        self.assertIn("round_wall_time_ms_p95", result.latency)


class EncryptedFederationTests(unittest.TestCase):
    def test_recovery_id_is_persistently_deduplicated(self):
        from agent_sidecar.store import LocalStore
        with tempfile.TemporaryDirectory() as temporary:
            store = LocalStore(Path(temporary) / "state.db")
            self.assertTrue(store.record_fl_recovery("rec-1", "r1", "node-a", "node-b", "sha256:mask"))
            self.assertFalse(store.record_fl_recovery("rec-1", "r1", "node-a", "node-b", "sha256:mask"))

    def test_round_and_update_replay_are_persistently_rejected(self):
        from agent_sidecar.store import LocalStore
        with tempfile.TemporaryDirectory() as temporary:
            store = LocalStore(Path(temporary) / "state.db")
            self.assertTrue(store.claim_fl_round("r1", "n1", "sha256:model"))
            self.assertFalse(store.claim_fl_round("r1", "n1", "sha256:model"))
            with self.assertRaises(ValueError):
                store.claim_fl_round("r1", "different", "sha256:model")
            self.assertTrue(store.record_fl_update("u1", "r1", "node-1", "sha256:model"))
            self.assertFalse(store.record_fl_update("u1", "r1", "node-1", "sha256:model"))

    def test_secure_round_recovers_one_dropped_participant_mask(self):
        from agent_sidecar.federated.simulator import _dataset
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True); relay_thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                coordinator_sidecar = Sidecar(root / "coordinator", relay_url)
                nodes = [Sidecar(root / f"node-{i}", relay_url) for i in range(2)]
                for node in nodes:
                    coordinator_sidecar.trust(node.identity.agent_id, node.identity.public().signing_key)
                    node.trust(coordinator_sidecar.identity.agent_id, coordinator_sidecar.identity.public().signing_key)
                nodes[0].trust(nodes[1].identity.agent_id, nodes[1].identity.public().signing_key)
                nodes[1].trust(nodes[0].identity.agent_id, nodes[0].identity.public().signing_key)
                ids = [node.identity.agent_id for node in nodes]
                coordinator = FederatedCoordinator(coordinator_sidecar, ToyHarness(_dataset(192, 10, 99, False)), ids, ToyHarness.initial_model(), strategy="secure_fedavg", secure_max_dropouts=1)
                worker = FederatedNode(nodes[0], ToyHarness(_dataset(192, 20, 0, False)), coordinator_sidecar.identity.agent_id)
                config = coordinator.start_round(); worker.poll_once()
                result_holder = []
                error_holder = []
                def collect():
                    try:
                        result_holder.append(coordinator.collect_and_aggregate(config, timeout_seconds=1.0))
                    except Exception as exc:
                        error_holder.append(exc)
                collector = threading.Thread(target=collect); collector.start()
                deadline = time.time() + 2.5
                while collector.is_alive() and time.time() < deadline:
                    worker.poll_once()
                    time.sleep(0.05)
                collector.join(timeout=3)
                self.assertFalse(error_holder)
                self.assertEqual(len(result_holder[0].accepted_update_ids), 1)
            finally:
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)

    def test_secure_round_recovers_two_dropped_participants(self):
        from agent_sidecar.federated.simulator import _dataset
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True); relay_thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                coordinator_sidecar = Sidecar(root / "coordinator", relay_url)
                nodes = [Sidecar(root / f"node-{i}", relay_url) for i in range(3)]
                for node in nodes:
                    coordinator_sidecar.trust(node.identity.agent_id, node.identity.public().signing_key)
                    node.trust(coordinator_sidecar.identity.agent_id, coordinator_sidecar.identity.public().signing_key)
                for node in nodes:
                    for peer in nodes:
                        if peer.identity.agent_id != node.identity.agent_id:
                            node.trust(peer.identity.agent_id, peer.identity.public().signing_key)
                ids = [node.identity.agent_id for node in nodes]
                coordinator = FederatedCoordinator(coordinator_sidecar, ToyHarness(_dataset(193, 10, 99, False)), ids, ToyHarness.initial_model(), strategy="secure_fedavg", secure_max_dropouts=2)
                worker = FederatedNode(nodes[0], ToyHarness(_dataset(193, 20, 0, False)), coordinator_sidecar.identity.agent_id)
                config = coordinator.start_round(); worker.poll_once()
                result_holder = []; error_holder = []
                def collect():
                    try: result_holder.append(coordinator.collect_and_aggregate(config, timeout_seconds=1.0))
                    except Exception as exc: error_holder.append(exc)
                collector = threading.Thread(target=collect); collector.start()
                deadline = time.time() + 2.5
                while collector.is_alive() and time.time() < deadline:
                    worker.poll_once(); time.sleep(0.05)
                collector.join(timeout=3)
                self.assertFalse(error_holder)
                self.assertEqual(len(result_holder[0].accepted_update_ids), 1)
            finally:
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)

    def test_secure_round_aborts_when_participant_drops_out(self):
        from agent_sidecar.federated.simulator import _dataset
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True); relay_thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                coordinator_sidecar = Sidecar(root / "coordinator", relay_url)
                nodes = [Sidecar(root / f"node-{i}", relay_url) for i in range(2)]
                for node in nodes:
                    coordinator_sidecar.trust(node.identity.agent_id, node.identity.public().signing_key)
                    node.trust(coordinator_sidecar.identity.agent_id, coordinator_sidecar.identity.public().signing_key)
                for node in nodes:
                    other = nodes[1] if node is nodes[0] else nodes[0]
                    node.trust(other.identity.agent_id, other.identity.public().signing_key)
                ids = [node.identity.agent_id for node in nodes]
                coordinator = FederatedCoordinator(coordinator_sidecar, ToyHarness(_dataset(191, 10, 99, False)), ids, ToyHarness.initial_model(), strategy="secure_fedavg")
                worker = FederatedNode(nodes[0], ToyHarness(_dataset(191, 20, 0, False)), coordinator_sidecar.identity.agent_id)
                config = coordinator.start_round(); worker.poll_once()
                with self.assertRaises(TimeoutError):
                    coordinator.collect_and_aggregate(config, timeout_seconds=0.05)
                self.assertEqual(coordinator_sidecar.store.fl_round(config.round_id)["status"], "aborted")
            finally:
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)

    def test_secure_federated_round_uses_pairwise_masks(self):
        from agent_sidecar.federated.simulator import _dataset
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True); relay_thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                coordinator_sidecar = Sidecar(root / "coordinator", relay_url)
                nodes = [Sidecar(root / f"node-{i}", relay_url) for i in range(3)]
                for node in nodes:
                    coordinator_sidecar.trust(node.identity.agent_id, node.identity.public().signing_key)
                    node.trust(coordinator_sidecar.identity.agent_id, coordinator_sidecar.identity.public().signing_key)
                for node in nodes:
                    for peer in nodes:
                        if peer.identity.agent_id != node.identity.agent_id:
                            node.trust(peer.identity.agent_id, peer.identity.public().signing_key)
                participant_ids = [node.identity.agent_id for node in nodes]
                coordinator = FederatedCoordinator(coordinator_sidecar, ToyHarness(_dataset(190, 10, 99, False)), participant_ids, ToyHarness.initial_model(), strategy="secure_fedavg")
                workers = [FederatedNode(node, ToyHarness(_dataset(190, 20, i, False)), coordinator_sidecar.identity.agent_id) for i, node in enumerate(nodes)]
                config = coordinator.start_round()
                for worker in workers: worker.poll_once()
                result = coordinator.collect_and_aggregate(config)
                self.assertEqual(len(result.accepted_update_ids), 3)
                self.assertTrue(result.robustness_stats["secure_aggregation"])
                for worker in workers: worker.poll_once()
            finally:
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)

    def test_pending_round_resumes_after_coordinator_restart(self):
        from agent_sidecar.federated.simulator import _dataset
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store)); relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True); relay_thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                coordinator_sidecar = Sidecar(root / "coordinator", relay_url); node = Sidecar(root / "node", relay_url)
                coordinator_sidecar.trust(node.identity.agent_id, node.identity.public().signing_key); node.trust(coordinator_sidecar.identity.agent_id, coordinator_sidecar.identity.public().signing_key)
                first = FederatedCoordinator(coordinator_sidecar, ToyHarness(_dataset(195, 10, 9, False)), [node.identity.agent_id], ToyHarness.initial_model())
                config = first.start_round(); FederatedNode(node, ToyHarness(_dataset(195, 20, 0, False)), coordinator_sidecar.identity.agent_id).poll_once(); first.release_lease()
                restarted = FederatedCoordinator(coordinator_sidecar, ToyHarness(_dataset(195, 10, 9, False)), [node.identity.agent_id], ToyHarness.initial_model())
                self.assertIsNotNone(restarted.pending_round())
                result = restarted.resume_pending_round(timeout_seconds=1.0)
                self.assertEqual(len(result.accepted_update_ids), 1)
            finally:
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)

    def test_coordinator_lease_prevents_concurrent_owners(self):
        from agent_sidecar.store import LocalStore
        with tempfile.TemporaryDirectory() as temporary:
            store = LocalStore(Path(temporary) / "state.db")
            self.assertTrue(store.acquire_fl_coordinator_lease("owner-a", 30))
            self.assertFalse(store.acquire_fl_coordinator_lease("owner-b", 30))
            self.assertTrue(store.release_fl_coordinator_lease("owner-a"))
            self.assertTrue(store.acquire_fl_coordinator_lease("owner-b", 30))

    def test_coordinator_state_restores_after_restart(self):
        from agent_sidecar.federated.simulator import _dataset
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store)); relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True); relay_thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                coordinator_sidecar = Sidecar(root / "coordinator", relay_url); node = Sidecar(root / "node", relay_url)
                coordinator_sidecar.trust(node.identity.agent_id, node.identity.public().signing_key); node.trust(coordinator_sidecar.identity.agent_id, coordinator_sidecar.identity.public().signing_key)
                coordinator = FederatedCoordinator(coordinator_sidecar, ToyHarness(_dataset(194, 10, 9, False)), [node.identity.agent_id], ToyHarness.initial_model())
                worker = FederatedNode(node, ToyHarness(_dataset(194, 20, 0, False)), coordinator_sidecar.identity.agent_id)
                config = coordinator.start_round(); worker.poll_once(); result = coordinator.collect_and_aggregate(config)
                restarted = FederatedCoordinator(coordinator_sidecar, ToyHarness(_dataset(194, 10, 9, False)), [node.identity.agent_id], ToyHarness.initial_model())
                self.assertEqual(restarted.round_index, 1)
                self.assertEqual(restarted.global_model, coordinator.global_model)
            finally:
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)

    def test_federation_uses_project_scoped_trust_and_stops_after_close(self):
        from agent_sidecar.federated.simulator import _dataset
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store)); relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True); relay_thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                coordinator_sidecar = Sidecar(root / "coordinator", relay_url); node = Sidecar(root / "node", relay_url)
                project_id = "project-fl-temporary"
                coordinator_sidecar.create_project("FL 项目", project_id)
                node.create_project("FL 项目", project_id, owner_id=coordinator_sidecar.identity.agent_id)
                coordinator_sidecar.trust_for_project(project_id, node.identity.agent_id)
                node.trust_for_project(project_id, coordinator_sidecar.identity.agent_id)
                coordinator = FederatedCoordinator(coordinator_sidecar, ToyHarness(_dataset(196, 10, 9, False)), [node.identity.agent_id], ToyHarness.initial_model(), project_id=project_id)
                worker = FederatedNode(node, ToyHarness(_dataset(196, 20, 0, False)), coordinator_sidecar.identity.agent_id)
                config = coordinator.start_round(); worker.poll_once(); result = coordinator.collect_and_aggregate(config)
                self.assertEqual(len(result.accepted_update_ids), 1)
                coordinator_sidecar.close_project(project_id); worker.poll_once()
                self.assertEqual(node.store.project(project_id)["status"], "closed")
                with self.assertRaises(ValueError):
                    FederatedCoordinator(coordinator_sidecar, ToyHarness(_dataset(196, 10, 9, False)), [node.identity.agent_id], coordinator.global_model, project_id=project_id).start_round()
            finally:
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)

    def test_coordinator_and_nodes_complete_rounds_over_sidecar(self):
        from agent_sidecar.federated.simulator import _dataset
        from agent_sidecar.federated.runtime import FederatedCoordinator, FederatedNode
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True)
            relay_thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                coordinator_sidecar = Sidecar(root / "coordinator", relay_url)
                nodes = [Sidecar(root / f"node-{i}", relay_url) for i in range(3)]
                for node in nodes:
                    coordinator_sidecar.trust(node.identity.agent_id, node.identity.public().signing_key)
                    node.trust(coordinator_sidecar.identity.agent_id, coordinator_sidecar.identity.public().signing_key)
                coordinator = FederatedCoordinator(coordinator_sidecar, ToyHarness(_dataset(90, 10, 99, False)), [n.identity.agent_id for n in nodes], ToyHarness.initial_model())
                workers = [FederatedNode(n, ToyHarness(_dataset(90, 20, i, False)), coordinator_sidecar.identity.agent_id) for i, n in enumerate(nodes)]
                for _ in range(2):
                    config = coordinator.start_round()
                    for worker in workers: worker.poll_once()
                    result = coordinator.collect_and_aggregate(config)
                    for worker in workers: worker.poll_once()
                    self.assertEqual(len(result.accepted_update_ids), 3)
                self.assertEqual(coordinator.round_index, 2)
            finally:
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)


class ArtifactTests(unittest.TestCase):
    def test_chunk_round_trip_and_tamper_detection(self):
        payload = b"a" * 17 + b"b" * 9
        chunks = chunk_artifact("artifact-1", payload, chunk_size=10)
        self.assertEqual(assemble_artifact(chunks), payload)
        tampered = list(chunks)
        tampered[1] = type(tampered[1])(**{**tampered[1].__dict__, "payload": b"tampered"})
        with self.assertRaises(ValueError):
            assemble_artifact(tampered)


class HarnessTests(unittest.TestCase):
    def test_preflight_reports_toy_harness_without_secrets(self):
        report = run_preflight(harness_config=Path("configs/harness/toy.example.json"))
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["harness"], "toy")
        self.assertNotIn("sk-", json.dumps(report))
        self.assertNotIn("secret", json.dumps(report).lower())

    def test_harness_factory_loads_deepseek_descriptor(self):
        adapter = load_harness(Path("configs/harness/deepseek.example.json"))
        self.assertEqual(adapter.describe().name, "deepseek")
        self.assertTrue(adapter.describe().capabilities["lora"])

    def test_dsh_controller_adapter_uses_task_api(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _DshMockHandler.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            adapter = DshControllerAdapter(f"http://127.0.0.1:{server.server_port}", "token", timeout=2, poll_interval=0.01)
            self.assertTrue(adapter.describe().capabilities["task_execution"])
            result = adapter.execute_task("hello", task_id="task-1")
            self.assertTrue(result["success"])
            self.assertEqual(result["output"], "hello")
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_dsh_doctor_does_not_expose_secret_and_reports_missing_binary(self):
        report = diagnose_dsh("definitely-not-a-real-dsh", "headless")
        self.assertFalse(report["installed"])
        self.assertNotIn("sk-", json.dumps(report))
        self.assertNotIn("secret", json.dumps(report).lower())

    def test_dsh_headless_adapter_parses_fake_executable_result(self):
        import os, stat
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "fake-dsh"
            script.write_text("#!/bin/sh\nprintf '%s' '{\"model\":{\"weights\":[0.4],\"bias\":[0.1]},\"num_examples\":4,\"metrics\":{\"accuracy\":0.9}}'\n")
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
            adapter = DshHeadlessAdapter(str(script), timeout=2)
            result = adapter.train(TrainRequest("r1", "toy", "sha256:base", {"weights": [0.0], "bias": [0.0]}, 1, 0.1))
            self.assertEqual(result.num_examples, 4)
            self.assertEqual(result.model["weights"], [0.4])

    def test_dsh_training_adapter_parses_structured_result(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _DshTrainingMockHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            adapter = DshControllerTrainingAdapter(f"http://127.0.0.1:{server.server_port}", "token", timeout=2, poll_interval=0.01)
            request = TrainRequest("r1", "toy", "sha256:base", {"weights": [0.0, 0.0], "bias": [0.0]}, 1, 0.1)
            result = adapter.train(request)
            self.assertEqual(result.num_examples, 8)
            self.assertEqual(result.model["weights"], [0.2, 0.3])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_http_harness_contract(self):
        from agent_sidecar.federated.simulator import _dataset
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_harness_handler(__import__("agent_sidecar.harness", fromlist=["ToyHarness"]).ToyHarness(_dataset(5, 20, 0, False))))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            adapter = HttpHarnessAdapter(f"http://127.0.0.1:{server.server_port}")
            self.assertEqual(adapter.describe().name, "toy")
            model = __import__("agent_sidecar.harness", fromlist=["ToyHarness"]).ToyHarness.initial_model()
            result = adapter.train(TrainRequest("r1", "toy-linear-v1", model_hash(model), model, 2, 0.1))
            self.assertIn("accuracy", result.metrics)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


class CapabilityBroadcastTests(unittest.TestCase):
    def test_node_login_broadcasts_hardware_and_network_capabilities(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store)); relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True); relay_thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                alice = Sidecar(root / "alice", relay_url); bob = Sidecar(root / "bob", relay_url)
                self.assertEqual(alice.capabilities["schema"], "sidecar-node-capabilities/1")
                self.assertIn("gpu", alice.capabilities["hardware"])
                self.assertIn("camera", alice.capabilities["hardware"])
                self.assertIn("stability", alice.capabilities["network"])
                nodes = bob.capability_nodes()
                sender_ids = {node["sender_id"] for node in nodes}
                self.assertIn(alice.identity.agent_id, sender_ids)
                self.assertIn(bob.identity.agent_id, sender_ids)
            finally:
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)

class BroadcastTests(unittest.TestCase):
    def test_public_broadcast_visible_without_peer_trust_and_bootstrap_installs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store)); relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True); relay_thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                alice = Sidecar(root / "alice", relay_url); bob = Sidecar(root / "bob", relay_url)
                broadcast = alice.publish_broadcast("project", "# 招募\n需要一个数据分析节点", tags=["recruitment"])
                received = bob.broadcasts()
                self.assertEqual(received[0]["broadcast_id"], broadcast["broadcast_id"])
                first_trace_count = len(bob.store.traces())
                bob.broadcasts()
                self.assertEqual(len(bob.store.traces()), first_trace_count)
                self.assertEqual(received[0]["content_type"], "markdown")
                self.assertTrue((root / "bob" / "mcp" / "manifest.json").exists())
                self.assertTrue((root / "bob" / "trace" / "schema.json").exists())
                self.assertTrue((root / "bob" / "skills" / "manifest.schema.json").exists())
                self.assertIsNotNone(bob.store.bootstrap())
                bob.install_skill({"skill_id": "local.skill", "name": "Local Skill", "version": "0.1.0", "capabilities": ["broadcast.read"]})
                self.assertEqual(bob.store.skills()[0]["skill_id"], "local.skill")
                with self.assertRaises(ValueError):
                    bob.install_skill({"skill_id": "../unsafe", "name": "Bad", "version": "0.1.0"})
                with self.assertRaises(ValueError):
                    bob.install_skill({"skill_id": "bad-exec", "name": "Bad", "version": "0.1.0", "execution": "shell"})
                self.assertTrue((root / "bob" / "skills" / "local.skill.json").exists())
                ui_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(bob))
                ui_thread = threading.Thread(target=ui_server.serve_forever, daemon=True); ui_thread.start()
                try:
                    with urlopen(f"http://127.0.0.1:{ui_server.server_port}/v1/mcp/manifest") as response:
                        self.assertTrue(any(tool["name"] == "broadcast.publish" for tool in json.loads(response.read())["tools"]))
                    request = Request(f"http://127.0.0.1:{ui_server.server_port}/mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
                    with urlopen(request) as response:
                        tools = json.loads(response.read())["result"]["tools"]
                        self.assertTrue(any(tool["name"] == "skill.install" for tool in tools))
                        # Project trust is exposed to Harnesses through the same MCP surface.
                        self.assertTrue(any(tool["name"] == "project.close" for tool in tools))
                finally:
                    ui_server.shutdown(); ui_server.server_close(); ui_thread.join(timeout=2)
            finally:
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)

class TrustUITests(unittest.TestCase):
    def test_untrusted_peer_cannot_send(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            thread = threading.Thread(target=relay_server.serve_forever, daemon=True)
            thread.start()
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                alice = Sidecar(root / "alice", relay_url)
                bob = Sidecar(root / "bob", relay_url)
                with self.assertRaises(ValueError):
                    alice.send(bob.identity.agent_id, "fl.update.submit", {"round_id": "r1"})
            finally:
                relay_server.shutdown(); relay_server.server_close(); thread.join(timeout=2)

    def test_federated_audit_endpoints_expose_rounds_and_recoveries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True); relay_thread.start()
            sidecar_server = None
            try:
                relay_url = f"http://127.0.0.1:{relay_server.server_port}"
                sidecar = Sidecar(root / "sidecar", relay_url)
                sidecar.store.claim_fl_round("r-audit", "nonce", "sha256:model")
                sidecar.store.record_fl_recovery("rec-audit", "r-audit", "node-a", "node-b", "sha256:mask")
                sidecar_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(sidecar))
                sidecar_thread = threading.Thread(target=sidecar_server.serve_forever, daemon=True); sidecar_thread.start()
                with urlopen(f"http://127.0.0.1:{sidecar_server.server_port}/v1/federated/rounds") as response:
                    self.assertEqual(json.loads(response.read())["rounds"][0]["round_id"], "r-audit")
                with urlopen(f"http://127.0.0.1:{sidecar_server.server_port}/v1/federated/recoveries") as response:
                    self.assertEqual(json.loads(response.read())["recoveries"][0]["recovery_id"], "rec-audit")
                sidecar_server.shutdown(); sidecar_server.server_close(); sidecar_thread.join(timeout=2)
            finally:
                if sidecar_server is not None:
                    try: sidecar_server.server_close()
                    except Exception: pass
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)

    def test_new_node_candidates_are_pending_and_can_be_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay_store = RelayStore(root / "relay.db")
            relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
            relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True)
            relay_thread.start()
            relay_url = f"http://127.0.0.1:{relay_server.server_port}"
            alice = Sidecar(root / "alice", relay_url)
            bob = Sidecar(root / "bob", relay_url)
            sidecar_server = ThreadingHTTPServer(("127.0.0.1", 0), make_sidecar_handler(alice))
            sidecar_thread = threading.Thread(target=sidecar_server.serve_forever, daemon=True)
            sidecar_thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{sidecar_server.server_port}/v1/peers/candidates") as response:
                    candidates = json.loads(response.read().decode("utf-8"))["peers"]
                self.assertEqual(candidates[0]["trust_state"], "pending")
                request = Request(f"http://127.0.0.1:{sidecar_server.server_port}/v1/peers/trust", data=json.dumps({"trusted_agent_ids": [bob.identity.agent_id]}).encode(), headers={"Content-Type": "application/json"}, method="PUT")
                with urlopen(request) as response:
                    body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(body["trusted_agent_ids"], [bob.identity.agent_id])
                self.assertIn(bob.identity.agent_id, alice.store.trusted_ids())
                with urlopen(f"http://127.0.0.1:{sidecar_server.server_port}/ui/trust") as response:
                    self.assertIn("信任管理".encode("utf-8"), response.read())
            finally:
                sidecar_server.shutdown(); sidecar_server.server_close(); sidecar_thread.join(timeout=2)
                relay_server.shutdown(); relay_server.server_close(); relay_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

"""Run a real encrypted sidecar-to-sidecar federated-learning round sequence."""
from __future__ import annotations

import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_sidecar.federated.runtime import FederatedCoordinator, FederatedNode
from agent_sidecar.federated.simulator import _dataset
from agent_sidecar.harness import ToyHarness
from agent_sidecar.relay import make_handler
from agent_sidecar.service import Sidecar
from agent_sidecar.store import RelayStore


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="sidecar-fl-demo-") as temporary:
        root = Path(temporary)
        relay_store = RelayStore(root / "relay.db")
        relay_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
        relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True)
        relay_thread.start()
        relay_url = f"http://127.0.0.1:{relay_server.server_port}"
        try:
            coordinator_sidecar = Sidecar(root / "coordinator", relay_url)
            nodes = [Sidecar(root / f"node-{i}", relay_url) for i in range(3)]
            for node in nodes:
                coordinator_sidecar.trust(node.identity.agent_id, node.identity.public().signing_key)
                node.trust(coordinator_sidecar.identity.agent_id, coordinator_sidecar.identity.public().signing_key)
            coordinator = FederatedCoordinator(
                coordinator_sidecar,
                ToyHarness(_dataset(123, 40, 99, False)),
                [node.identity.agent_id for node in nodes],
                ToyHarness.initial_model(),
                strategy="fedavg",
            )
            workers = [FederatedNode(node, ToyHarness(_dataset(123, 40, i, False)), coordinator_sidecar.identity.agent_id) for i, node in enumerate(nodes)]
            completed = []
            for _ in range(3):
                config = coordinator.start_round()
                for worker in workers:
                    worker.poll_once()
                result = coordinator.collect_and_aggregate(config)
                completed.append({"round_id": config.round_id, "accepted": result.accepted_update_ids})
                for worker in workers:
                    worker.poll_once()
            print(json.dumps({"rounds": completed, "model": coordinator.global_model}, ensure_ascii=False, indent=2))
        finally:
            relay_server.shutdown()
            relay_server.server_close()
            relay_thread.join(timeout=2)

"""Run the deterministic toy federated-learning flow."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_sidecar.federated.simulator import SimulationConfig, run_simulation, write_benchmark


if __name__ == "__main__":
    result = run_simulation(SimulationConfig(nodes=5, rounds=10, strategy="trimmed_mean", malicious_nodes=1, attack="scale", max_byzantine=0))
    output = Path("runs/federated-demo")
    write_benchmark(result, output)
    print(json.dumps({"nodes": 5, "rounds": 10, "strategy": result.config["strategy"], "final_metrics": result.final_metrics, "security": result.security, "output": str(output)}, ensure_ascii=False, indent=2))

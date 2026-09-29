from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import List, Optional

from .aggregators import available_aggregators
from .simulator import SimulationConfig, run_simulation, write_benchmark


def write_comparison_report(results: List[dict], output_dir: Path) -> None:
    rows = []
    for result in results:
        config = result["config"]
        security = result["security"]
        communication = result["communication"]
        latency = result["latency"]
        rows.append({
            "topology": config.get("topology", "star"),
            "strategy": config["strategy"],
            "attack": config["attack"],
            "final_accuracy": result["final_metrics"]["accuracy"],
            "accuracy_drop_vs_clean": security["accuracy_drop_vs_clean"],
            "malicious_influence_proxy": security["malicious_influence_proxy"],
            "network_bytes": communication.get("network_bytes", communication.get("bytes_sent", 0)),
            "coordinator_messages": communication.get("coordinator_messages", communication.get("total_messages", 0)),
            "round_latency_ms_mean": latency["round_wall_time_ms_mean"],
            "round_latency_ms_p95": latency["round_wall_time_ms_p95"],
        })
    fields = list(rows[0]) if rows else ["topology", "strategy", "attack"]
    with (output_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    clean_rows = [row for row in rows if row["attack"] == "none"]
    clean_rows.sort(key=lambda row: (row["accuracy_drop_vs_clean"], row["round_latency_ms_mean"], row["network_bytes"]))
    robust_rows = [row for row in rows if row["attack"] != "none"]
    robust_rows.sort(key=lambda row: (row["malicious_influence_proxy"], row["accuracy_drop_vs_clean"], row["round_latency_ms_mean"]))
    lines = [
        "# Federated Learning Comparison Report",
        "",
        "This report ranks completed runs. It is an experimental comparison, not a formal security proof.",
        "",
        "## Clean scenario ranking",
        "",
        "| Rank | Topology | Strategy | Accuracy | Mean latency ms | Network bytes | Coordinator messages |",
        "|---:|---|---|---:|---:|---:|---:|",
    ]
    for index, row in enumerate(clean_rows, 1):
        lines.append(f"| {index} | {row['topology']} | {row['strategy']} | {row['final_accuracy']:.4f} | {row['round_latency_ms_mean']:.2f} | {int(row['network_bytes'])} | {int(row['coordinator_messages'])} |")
    lines += ["", "## Attack robustness ranking", "", "| Rank | Topology | Strategy | Attack | Influence proxy | Accuracy drop | Mean latency ms |", "|---:|---|---|---|---:|---:|---:|"]
    for index, row in enumerate(robust_rows[:25], 1):
        lines.append(f"| {index} | {row['topology']} | {row['strategy']} | {row['attack']} | {row['malicious_influence_proxy']:.4f} | {row['accuracy_drop_vs_clean']:.4f} | {row['round_latency_ms_mean']:.2f} |")
    (output_dir / "comparison.md").write_text("\n".join(lines) + "\n", "utf-8")

def run_matrix(output_dir: Path, nodes: int = 8, rounds: int = 30, seed: int = 7, topologies: Optional[List[str]] = None, dp_clip_norm: float = 0.0, dp_noise_std: float = 0.0) -> List[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    strategies = ["fedavg", "fedprox", "secure_fedavg", "median", "trimmed_mean", "krum"]
    scenarios = [("none", 0), ("label_flip", 1), ("scale", 1), ("sign_flip", 1), ("random_byzantine", 1)]
    topologies = topologies or ["star", "hierarchical", "gossip"]
    for topology in topologies:
        for strategy in strategies:
            for attack, malicious in scenarios:
                max_byzantine = 1 if strategy == "krum" else 0
                result = run_simulation(SimulationConfig(nodes=nodes, rounds=rounds, strategy=strategy, topology=topology, attack=attack, malicious_nodes=malicious, seed=seed, dp_clip_norm=dp_clip_norm, dp_noise_std=dp_noise_std, max_byzantine=max_byzantine))
                name = f"{topology}-{strategy}-{attack}"
                run_dir = output_dir / name
                write_benchmark(result, run_dir)
                results.append(result.as_dict())
    (output_dir / "matrix.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")
    write_comparison_report(results, output_dir)
    return results


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Run reproducible federated-learning benchmark")
    parser.add_argument("--output", type=Path, default=Path("runs/benchmark-small"))
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--nodes", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    if args.config:
        config = json.loads(args.config.read_text("utf-8"))
        args.nodes = int(config.get("nodes", args.nodes))
        args.rounds = int(config.get("rounds", args.rounds))
        args.seed = int(config.get("seed", args.seed))
        topologies = config.get("topologies")
        dp_clip_norm = float(config.get("dp_clip_norm", 0.0))
        dp_noise_std = float(config.get("dp_noise_std", 0.0))
    else:
        topologies = None
        dp_clip_norm = 0.0
        dp_noise_std = 0.0
    results = run_matrix(args.output, args.nodes, args.rounds, args.seed, topologies, dp_clip_norm, dp_noise_std)
    print(json.dumps({"runs": len(results), "output": str(args.output), "strategies": available_aggregators()}, ensure_ascii=False))


if __name__ == "__main__":
    main()

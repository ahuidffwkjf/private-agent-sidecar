from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .federated.benchmark import run_matrix
from .federated.simulator import SimulationConfig, run_simulation, write_benchmark, _dataset
from .federated.runtime import FederatedCoordinator, FederatedNode
from .harness import ToyHarness, diagnose_dsh
from .harness_server import serve_harness
from .harness_factory import load_harness
from .preflight import run_preflight
from .relay import serve_relay
from .service import serve_sidecar


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-sidecar", description="Encrypted messaging and federated learning for autonomous agents")
    commands = parser.add_subparsers(dest="command", required=True)

    relay = commands.add_parser("relay", help="run the untrusted ciphertext relay")
    relay.add_argument("--host", default="127.0.0.1")
    relay.add_argument("--port", type=int, default=8787)
    relay.add_argument("--database", type=Path, default=Path("relay.db"))
    relay.add_argument("--tls-cert", type=Path, default=None)
    relay.add_argument("--tls-key", type=Path, default=None)
    relay.add_argument("--access-token", default="")
    relay.add_argument("--rate-limit", type=int, default=0, help="max protected requests per source IP per minute; 0 disables")

    serve = commands.add_parser("serve", help="run one local agent sidecar")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--data-dir", type=Path, required=True)
    serve.add_argument("--relay", default="http://127.0.0.1:8787")
    serve.add_argument("--tls-cert", type=Path, default=None)
    serve.add_argument("--tls-key", type=Path, default=None)
    serve.add_argument("--relay-token", default="")

    preflight = commands.add_parser("preflight", help="validate local deployment prerequisites without printing secrets")
    preflight.add_argument("--data-dir", type=Path, default=None)
    preflight.add_argument("--harness-config", type=Path, default=None)
    preflight.add_argument("--relay", default=None)
    preflight.add_argument("--tls-cert", type=Path, default=None)
    preflight.add_argument("--tls-key", type=Path, default=None)

    federated = commands.add_parser("federated", help="run federated-learning flows")
    federated_sub = federated.add_subparsers(dest="federated_command", required=True)
    run = federated_sub.add_parser("run", help="run a local deterministic federation")
    run.add_argument("--nodes", type=int, default=5)
    run.add_argument("--rounds", type=int, default=10)
    run.add_argument("--strategy", default="fedavg")
    run.add_argument("--topology", default="star", choices=("star", "hierarchical", "gossip"))
    run.add_argument("--attack", default="none")
    run.add_argument("--dp-clip-norm", type=float, default=0.0)
    run.add_argument("--dp-noise-std", type=float, default=0.0)
    run.add_argument("--malicious-nodes", type=int, default=0)
    run.add_argument("--output", type=Path, default=Path("runs/federated-run"))

    coordinator = federated_sub.add_parser("coordinator", help="run a sidecar-backed coordinator")
    coordinator.add_argument("--data-dir", type=Path, required=True)
    coordinator.add_argument("--relay", default="http://127.0.0.1:8787")
    coordinator.add_argument("--participants", default="", help="comma-separated agent ids")
    coordinator.add_argument("--config", type=Path, default=None)
    coordinator.add_argument("--harness-config", type=Path, default=None)
    coordinator.add_argument("--rounds", type=int, default=1)
    coordinator.add_argument("--strategy", default="fedavg")
    coordinator.add_argument("--timeout", type=float, default=30.0)

    node = federated_sub.add_parser("node", help="run a sidecar-backed federated worker")
    node.add_argument("--data-dir", type=Path, required=True)
    node.add_argument("--relay", default="http://127.0.0.1:8787")
    node.add_argument("--coordinator-id", default="")
    node.add_argument("--config", type=Path, default=None)
    node.add_argument("--harness-config", type=Path, default=None)
    node.add_argument("--seed", type=int, default=7)
    node.add_argument("--duration", type=float, default=60.0)

    harness = commands.add_parser("harness", help="run and inspect cross-harness adapters")
    harness_sub = harness.add_subparsers(dest="harness_command", required=True)
    harness_serve = harness_sub.add_parser("serve", help="run the standard HTTP Harness contract")
    harness_serve.add_argument("--host", default="127.0.0.1")
    harness_serve.add_argument("--port", type=int, default=8900)
    harness_serve.add_argument("--seed", type=int, default=7)
    harness_describe = harness_sub.add_parser("describe", help="print a Harness descriptor from config")
    harness_describe.add_argument("--config", type=Path, required=True)
    harness_doctor = harness_sub.add_parser("doctor", help="check local DSH installation without printing secrets")
    harness_doctor.add_argument("--dsh-bin", default="dsh")
    harness_doctor.add_argument("--profile", default="headless")

    benchmark = commands.add_parser("benchmark", help="run the reproducible FL security/communication/latency benchmark")
    benchmark.add_argument("--config", type=Path, default=None)
    benchmark.add_argument("--output", type=Path, default=Path("runs/benchmark-small"))
    benchmark.add_argument("--nodes", type=int, default=8)
    benchmark.add_argument("--rounds", type=int, default=30)
    benchmark.add_argument("--seed", type=int, default=7)

    args = parser.parse_args()
    if args.command == "preflight":
        print(json.dumps(run_preflight(args.data_dir, args.harness_config, args.relay, args.tls_cert, args.tls_key), ensure_ascii=False, indent=2))
    elif args.command == "harness" and args.harness_command == "serve":
        serve_harness(args.host, args.port, args.seed)
    elif args.command == "harness" and args.harness_command == "describe":
        print(json.dumps(__import__("dataclasses").asdict(load_harness(args.config).describe()), ensure_ascii=False, indent=2))
    elif args.command == "harness" and args.harness_command == "doctor":
        print(json.dumps(diagnose_dsh(args.dsh_bin, args.profile), ensure_ascii=False, indent=2))
    elif args.command == "relay":
        serve_relay(args.host, args.port, args.database, args.tls_cert, args.tls_key, args.access_token, args.rate_limit)
    elif args.command == "serve":
        serve_sidecar(args.host, args.port, args.data_dir, args.relay, args.tls_cert, args.tls_key, args.relay_token)
    elif args.command == "benchmark":
        topologies = None
        if args.config:
            config = json.loads(args.config.read_text("utf-8"))
            args.nodes = int(config.get("nodes", args.nodes))
            args.rounds = int(config.get("rounds", args.rounds))
            args.seed = int(config.get("seed", args.seed))
            topologies = config.get("topologies")
            dp_clip_norm = float(config.get("dp_clip_norm", 0.0))
            dp_noise_std = float(config.get("dp_noise_std", 0.0))
        else:
            dp_clip_norm = 0.0
            dp_noise_std = 0.0
        results = run_matrix(args.output, args.nodes, args.rounds, args.seed, topologies, dp_clip_norm, dp_noise_std)
        print(json.dumps({"runs": len(results), "output": str(args.output)}, ensure_ascii=False))
    elif args.command == "federated" and args.federated_command == "run":
        result = run_simulation(SimulationConfig(nodes=args.nodes, rounds=args.rounds, strategy=args.strategy, topology=args.topology, attack=args.attack, dp_clip_norm=args.dp_clip_norm, dp_noise_std=args.dp_noise_std, malicious_nodes=args.malicious_nodes, max_byzantine=max(0, args.malicious_nodes)))
        write_benchmark(result, args.output)
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    elif args.command == "federated" and args.federated_command == "coordinator":
        sidecar = __import__("agent_sidecar.service", fromlist=["Sidecar"]).Sidecar(args.data_dir, args.relay)
        config = json.loads(args.config.read_text("utf-8")) if args.config else {}
        participant_ids = [value for value in (args.participants or ",".join(config.get("participants", []))).split(",") if value]
        if not participant_ids:
            raise SystemExit("coordinator requires --participants or a config with participants")
        coordinator_harness = load_harness(args.harness_config) if args.harness_config else ToyHarness(_dataset(100, 10, 999, False))
        coordinator_runtime = FederatedCoordinator(sidecar, coordinator_harness, participant_ids, ToyHarness.initial_model(), strategy=config.get("strategy", args.strategy), learning_rate=float(config.get("learning_rate", 0.2)), local_steps=int(config.get("local_steps", 8)), proximal_mu=float(config.get("proximal_mu", 0.01)))
        completed = []
        for _ in range(int(config.get("rounds", args.rounds))):
            config = coordinator_runtime.start_round()
            result = coordinator_runtime.collect_and_aggregate(config, args.timeout)
            completed.append({"round_id": config.round_id, "accepted": result.accepted_update_ids, "rejected": result.rejected_update_ids})
        print(json.dumps({"completed": completed, "model": coordinator_runtime.global_model}, ensure_ascii=False, indent=2))
    elif args.command == "federated" and args.federated_command == "node":
        sidecar = __import__("agent_sidecar.service", fromlist=["Sidecar"]).Sidecar(args.data_dir, args.relay)
        config = json.loads(args.config.read_text("utf-8")) if args.config else {}
        coordinator_id = args.coordinator_id or config.get("coordinator_id", "")
        if not coordinator_id:
            raise SystemExit("node requires --coordinator-id or a config with coordinator_id")
        worker_harness = load_harness(args.harness_config) if args.harness_config else ToyHarness(_dataset(int(config.get("seed", args.seed)), 40, int(config.get("seed", args.seed)), False))
        worker = FederatedNode(sidecar, worker_harness, coordinator_id)
        deadline = time.time() + float(config.get("duration", args.duration))
        handled = []
        while time.time() < deadline:
            handled.extend(worker.poll_once())
            time.sleep(0.1)
        print(json.dumps({"handled": handled, "agent_id": sidecar.identity.agent_id}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import csv
import json
import math
import random
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..harness import ToyHarness, encode_model
from .aggregators import get_aggregator
from .attacks import apply_attack
from .types import EvaluateRequest, NodeUpdate, RoundConfig, TrainRequest, clone_model, model_hash, participant_set_hash
from .secure import mask_updates_for_secure_sum
from .privacy import gaussian_rdp_accountant


@dataclass
class SimulationConfig:
    nodes: int = 5
    rounds: int = 10
    strategy: str = "fedavg"
    topology: str = "star"
    attack: str = "none"
    malicious_nodes: int = 0
    seed: int = 7
    local_steps: int = 8
    learning_rate: float = 0.2
    proximal_mu: float = 0.01
    dp_clip_norm: float = 0.0
    dp_noise_std: float = 0.0
    max_byzantine: int = 0
    non_iid: bool = False
    trusted_nodes: Optional[List[int]] = None


@dataclass
class SimulationResult:
    config: Dict[str, Any]
    rounds: List[Dict[str, Any]]
    final_model: Dict[str, List[float]]
    final_metrics: Dict[str, float]
    security: Dict[str, Any]
    communication: Dict[str, float]
    latency: Dict[str, float]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _dataset(seed: int, count: int, node_index: int, non_iid: bool, flip_labels: bool = False) -> List[tuple[list[float], int]]:
    rng = random.Random(seed + node_index * 1009)
    data = []
    for _ in range(count):
        label = rng.randint(0, 1)
        if non_iid and node_index % 2 == 0:
            label = 0 if rng.random() < 0.75 else label
        if flip_labels:
            label = 1 - label
        if label:
            features = [rng.gauss(1.5, 0.65), rng.gauss(1.2, 0.65)]
        else:
            features = [rng.gauss(-1.2, 0.65), rng.gauss(-1.5, 0.65)]
        data.append((features, label))
    return data



def _topology_transport(topology: str, participants: int, update_bytes: int, model_bytes: int) -> Dict[str, float]:
    """Estimate network cost for a round under a topology.

    The local simulator still uses one deterministic aggregator for model
    correctness. This function models the control/data-plane transmissions so
    the benchmark can compare communication structures without pretending to
    emulate a real network scheduler.
    """
    topology = topology.lower().strip()
    if topology not in {"star", "hierarchical", "gossip"}:
        raise ValueError("topology must be one of: star, hierarchical, gossip")
    if participants < 1:
        raise ValueError("participants must be positive")
    if topology == "star":
        messages = 2 * participants
        coordinator_messages = 2 * participants
        network_bytes = update_bytes + participants * model_bytes
        hops = 2
    elif topology == "hierarchical":
        group_size = 2
        groups = int(math.ceil(participants / group_size))
        node_to_group = participants - groups
        messages = node_to_group + groups + groups + node_to_group
        coordinator_messages = 2 * groups
        network_bytes = update_bytes + groups * model_bytes + groups * model_bytes + node_to_group * model_bytes
        hops = 4
    else:
        # One clockwise gossip pass for the update and one for the new model.
        messages = 2 * participants
        coordinator_messages = 0
        network_bytes = update_bytes + participants * model_bytes
        hops = participants
    return {
        "network_messages": float(messages),
        "coordinator_messages": float(coordinator_messages),
        "network_bytes": float(network_bytes),
        "network_hops": float(hops),
    }


def _apply_privacy(model: Dict[str, List[float]], global_model: Dict[str, List[float]], clip_norm: float, noise_std: float, rng: random.Random) -> Dict[str, List[float]]:
    if clip_norm <= 0 and noise_std <= 0:
        return clone_model(model)
    delta: Dict[str, List[float]] = {}
    squared_norm = 0.0
    for name, values in model.items():
        delta[name] = [float(value) - float(global_model[name][index]) for index, value in enumerate(values)]
        squared_norm += sum(value * value for value in delta[name])
    norm = math.sqrt(squared_norm)
    scale = min(1.0, clip_norm / norm) if clip_norm > 0 and norm > 0 else 1.0
    return {
        name: [float(global_model[name][index]) + value * scale + (rng.gauss(0.0, noise_std) if noise_std > 0 else 0.0) for index, value in enumerate(values)]
        for name, values in delta.items()
    }

def _run_once(config: SimulationConfig) -> SimulationResult:
    if config.nodes < 1 or config.rounds < 1:
        raise ValueError("nodes and rounds must be positive")
    if config.topology not in {"star", "hierarchical", "gossip"}:
        raise ValueError("topology must be one of: star, hierarchical, gossip")
    if config.malicious_nodes > config.nodes:
        raise ValueError("malicious_nodes cannot exceed nodes")
    trusted = set(range(config.nodes)) if config.trusted_nodes is None else set(config.trusted_nodes)
    if not trusted:
        raise ValueError("at least one trusted node is required")
    if config.malicious_nodes and len(trusted) < config.nodes:
        raise ValueError("benchmark cannot mark untrusted nodes malicious")
    datasets = [_dataset(config.seed, 40, i, config.non_iid, flip_labels=(config.attack == "label_flip" and i < config.malicious_nodes)) for i in range(config.nodes)]
    harnesses = [ToyHarness(data) for data in datasets]
    privacy_rng = random.Random(config.seed + 900001)
    test_harness = ToyHarness(_dataset(config.seed + 50000, 240, 0, False))
    global_model = ToyHarness.initial_model()
    rows: List[Dict[str, Any]] = []
    total_sent = total_received = total_messages = 0
    total_network_bytes = total_network_messages = total_coordinator_messages = 0.0
    malicious_submitted = malicious_accepted = honest_submitted = honest_rejected = 0
    malicious_selected_rounds = 0
    round_latencies: List[float] = []

    for round_index in range(1, config.rounds + 1):
        round_started = time.perf_counter()
        rid = f"round-{round_index:06d}"
        current_hash = model_hash(global_model)
        round_config = RoundConfig(
            rid,
            "toy-linear-v1",
            current_hash,
            config.strategy,
            config.local_steps,
            config.learning_rate,
            config.proximal_mu,
            min_clients=max(1, len(trusted) - config.malicious_nodes),
            max_byzantine=config.max_byzantine,
            nonce=f"nonce-{config.seed}-{round_index}",
            participants=tuple(f"node-{index}" for index in sorted(trusted)),
            secure_aggregation=config.strategy == "secure_fedavg",
            participant_set_hash=participant_set_hash(tuple(f"node-{index}" for index in sorted(trusted))),
        )
        updates: List[NodeUpdate] = []
        for node_index, harness in enumerate(harnesses):
            if node_index not in trusted:
                continue
            result = harness.train(TrainRequest(
                rid,
                round_config.model_id,
                current_hash,
                clone_model(global_model),
                config.local_steps,
                config.learning_rate,
                config.proximal_mu if config.strategy == "fedprox" else 0.0,
            ))
            private_model = _apply_privacy(result.model, global_model, config.dp_clip_norm, config.dp_noise_std, privacy_rng)
            update = NodeUpdate(
                f"update-{round_index}-{node_index}-{uuid.uuid4().hex[:8]}",
                f"node-{node_index}",
                rid,
                private_model,
                result.num_examples,
                config.local_steps,
                result.train_ms,
                result.metrics,
                current_hash,
                model_hash(private_model),
            )
            if node_index < config.malicious_nodes:
                update = apply_attack(update, config.attack, scale=10.0)
                malicious_submitted += 1
            else:
                honest_submitted += 1
            updates.append(update)
            total_sent += len(encode_model(update.model)) + 220
            total_messages += 1
        if len(updates) < round_config.min_clients:
            raise RuntimeError(f"round {rid} has only {len(updates)} updates; requires {round_config.min_clients}")
        if config.strategy == "secure_fedavg":
            updates = mask_updates_for_secure_sum(updates, rid, seed=config.seed, mask_scale=1.0)
        aggregator = get_aggregator(config.strategy)
        aggregation = aggregator.aggregate(global_model, updates, round_config)
        malicious_ids = {u.update_id for u in updates if u.malicious}
        malicious_accepted += len(malicious_ids.intersection(aggregation.accepted_update_ids))
        if config.strategy == "krum" and malicious_ids.intersection(aggregation.accepted_update_ids):
            malicious_selected_rounds += 1
        honest_rejected += len(set(aggregation.rejected_update_ids).difference(malicious_ids))
        global_model = aggregation.model
        eval_result = test_harness.evaluate(EvaluateRequest(round_config.model_id, model_hash(global_model), global_model))
        model_payload_bytes = len(encode_model(global_model))
        outbound_bytes = model_payload_bytes * len(updates) + 120
        update_payload_bytes = sum(len(encode_model(update.model)) + 220 for update in updates)
        transport = _topology_transport(config.topology, len(updates), update_payload_bytes, model_payload_bytes)
        total_received += outbound_bytes
        total_messages += len(updates)
        total_network_bytes += transport["network_bytes"]
        total_network_messages += transport["network_messages"]
        total_coordinator_messages += transport["coordinator_messages"]
        wall_latency_ms = (time.perf_counter() - round_started) * 1000
        network_latency_ms = transport["network_hops"] * 1.0
        latency_ms = wall_latency_ms + network_latency_ms
        round_latencies.append(latency_ms)
        rows.append({
            "round_id": rid,
            "strategy": config.strategy,
            "topology": config.topology,
            "participants": len(updates),
            "accepted": len(aggregation.accepted_update_ids),
            "rejected": len(aggregation.rejected_update_ids),
            "accuracy": eval_result.metrics["accuracy"],
            "loss": eval_result.metrics["loss"],
            "round_latency_ms": latency_ms,
            "wall_latency_ms": wall_latency_ms,
            "network_latency_ms": network_latency_ms,
            "aggregation_ms": aggregation.aggregation_ms,
            "bytes_sent": update_payload_bytes,
            "bytes_received": outbound_bytes,
            "update_hashes_validated": len(updates),
            "network_bytes": transport["network_bytes"],
            "network_messages": transport["network_messages"],
            "coordinator_messages": transport["coordinator_messages"],
            "fallback_reason": aggregation.fallback_reason or "",
        })

    final_metrics = {"accuracy": rows[-1]["accuracy"], "loss": rows[-1]["loss"]}
    malicious_ratio = config.malicious_nodes / max(1, len(trusted))
    if config.malicious_nodes == 0:
        influence_proxy = 0.0
    elif config.strategy in ("median", "coordinate_median") and config.malicious_nodes < len(trusted) / 2:
        influence_proxy = 0.0
    elif config.strategy == "trimmed_mean":
        trim = min(max(1, int(len(trusted) * 0.2)), (len(trusted) - 1) // 2)
        influence_proxy = max(0.0, (config.malicious_nodes - trim) / max(1, len(trusted)))
    elif config.strategy == "krum":
        influence_proxy = malicious_selected_rounds / max(1, config.rounds)
    else:
        influence_proxy = malicious_ratio
    privacy_report = gaussian_rdp_accountant(config.dp_noise_std, config.dp_clip_norm, config.rounds)
    security = {
        "malicious_update_accept_rate": malicious_accepted / malicious_submitted if malicious_submitted else 0.0,
        "honest_update_reject_rate": honest_rejected / honest_submitted if honest_submitted else 0.0,
        "malicious_influence_proxy": influence_proxy,
        "accuracy_drop_vs_clean": 0.0,
        "clean_reference_accuracy": final_metrics["accuracy"],
        "attack": config.attack,
        "dp_clip_norm": config.dp_clip_norm,
        "dp_noise_std": config.dp_noise_std,
        "formal_privacy_guarantee": False,
        "privacy_accountant_status": "upper_bound_under_declared_assumptions" if privacy_report.epsilon is not None else "not_configured",
        "privacy_epsilon": privacy_report.epsilon,
        "privacy_delta": privacy_report.delta,
        "privacy_accountant": privacy_report.accountant,
        "privacy_assumptions": list(privacy_report.assumptions),
        "secure_aggregation": config.strategy == "secure_fedavg",
        "individual_updates_meaningful": config.strategy != "secure_fedavg",
    }
    latency_sorted = sorted(round_latencies)
    latency = {
        "round_wall_time_ms_mean": sum(round_latencies) / len(round_latencies),
        "round_wall_time_ms_p50": latency_sorted[len(latency_sorted) // 2],
        "round_wall_time_ms_p95": latency_sorted[min(len(latency_sorted) - 1, math.ceil(len(latency_sorted) * 0.95) - 1)],
    }
    communication = {
        "bytes_sent": float(total_sent),
        "bytes_received": float(total_received),
        "total_messages": float(total_messages),
        "average_update_bytes": float(total_sent / max(1, config.rounds * len(trusted))),
        "network_bytes": float(total_network_bytes),
        "network_messages": float(total_network_messages),
        "coordinator_messages": float(total_coordinator_messages),
    }
    return SimulationResult(asdict(config), rows, global_model, final_metrics, security, communication, latency)


def run_simulation(config: SimulationConfig) -> SimulationResult:
    result = _run_once(config)
    if config.attack not in ("none", "") or config.malicious_nodes:
        clean_config = SimulationConfig(**{**asdict(config), "attack": "none", "malicious_nodes": 0})
        clean = _run_once(clean_config)
        result.security["clean_reference_accuracy"] = clean.final_metrics["accuracy"]
        result.security["accuracy_drop_vs_clean"] = max(0.0, clean.final_metrics["accuracy"] - result.final_metrics["accuracy"])
    return result


def write_benchmark(result: SimulationResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "config.json").write_text(json.dumps(result.config, ensure_ascii=False, indent=2), "utf-8")
    (output_dir / "results.json").write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), "utf-8")
    with (output_dir / "per_round.csv").open("w", newline="", encoding="utf-8") as handle:
        rows = result.rounds
        writer = csv.DictWriter(handle, fieldnames=sorted(rows[0]) if rows else ["round_id"])
        writer.writeheader()
        writer.writerows(rows)
    with (output_dir / "per_round.jsonl").open("w", encoding="utf-8") as handle:
        for row in result.rounds:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    summary = [
        "# Federated Learning Benchmark",
        "",
        f"Strategy: `{result.config['strategy']}`",
        f"Topology: `{result.config['topology']}`",
        f"Attack: `{result.config['attack']}`",
        f"DP noise std: `{result.security['dp_noise_std']}`",
        f"Privacy epsilon: `{result.security['privacy_epsilon']}` at delta `{result.security['privacy_delta']}`",
        f"Final accuracy: `{result.final_metrics['accuracy']:.4f}`",
        f"Accuracy drop vs clean: `{result.security['accuracy_drop_vs_clean']:.4f}`",
        f"Malicious accept rate: `{result.security['malicious_update_accept_rate']:.4f}`",
        f"Malicious influence proxy: `{result.security['malicious_influence_proxy']:.4f}`",
        f"Mean round latency ms: `{result.latency['round_wall_time_ms_mean']:.2f}`",
        f"Total messages: `{int(result.communication['total_messages'])}`",
        f"Total bytes sent: `{int(result.communication['bytes_sent'])}`",
    ]
    (output_dir / "summary.md").write_text("\n".join(summary) + "\n", "utf-8")

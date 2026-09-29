from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional

from ..service import Sidecar
from .aggregators import get_aggregator
from .types import AggregationResult, NodeUpdate, RoundConfig, TrainRequest, EvaluateRequest, clone_model, model_hash, participant_set_hash
from .secure import mask_model_with_identity, pairwise_mask_contribution


class FederatedNode:
    """Sidecar-backed worker. It consumes encrypted FL round messages and submits updates."""

    def __init__(self, sidecar: Sidecar, harness: Any, coordinator_id: str):
        self.sidecar = sidecar
        self.harness = harness
        self.coordinator_id = coordinator_id
        self.processed_rounds: set[str] = set()
        self.round_configs: Dict[str, RoundConfig] = {}
        self.round_models: Dict[str, Dict[str, List[float]]] = {}
        self.current_model: Optional[Dict[str, List[float]]] = None

    def poll_once(self) -> List[Dict[str, Any]]:
        handled: List[Dict[str, Any]] = []
        for message in self.sidecar.receive(auto_ack=True):
            if message.get("sender_id") != self.coordinator_id:
                continue
            if message.get("type") == "fl.secure.recovery.request":
                payload = message.get("payload", {})
                round_id = payload.get("round_id", "")
                config = self.round_configs.get(round_id)
                global_model = self.round_models.get(round_id)
                if not config or not global_model or not config.secure_aggregation:
                    continue
                for missing_id in payload.get("missing_participants", []):
                    if missing_id == self.sidecar.identity.agent_id:
                        continue
                    peer = self.sidecar.store.peer(missing_id)
                    if peer is None or missing_id not in self.sidecar.store.trusted_ids():
                        continue
                    mask_model = pairwise_mask_contribution(self.sidecar.identity, self.sidecar.identity.agent_id, peer, round_id, global_model, config.mask_scale)
                    self.sidecar.send(self.coordinator_id, "fl.secure.recovery.submit", {
                        "recovery_id": uuid.uuid4().hex,
                        "round_id": round_id,
                        "node_id": self.sidecar.identity.agent_id,
                        "missing_participant_id": missing_id,
                        "participant_set_hash": config.participant_set_hash,
                        "mask_model": mask_model,
                        "mask_hash": model_hash(mask_model),
                    }, project_id=config.project_id)
                    handled.append({"round_id": round_id, "status": "recovery_submitted", "missing_participant_id": missing_id})
                continue
            if message.get("type") == "fl.round.result":
                self.apply_round_result(message)
                handled.append({"round_id": message.get("payload", {}).get("round_id"), "status": "model_applied"})
                continue
            if message.get("type") != "fl.round.request":
                continue
            payload = message.get("payload", {})
            round_id = payload["round_id"]
            config = RoundConfig(**payload["config"])
            if config.model_hash != model_hash(payload["global_model"]):
                raise ValueError("round request global model hash mismatch")
            self.round_configs[round_id] = config
            self.round_models[round_id] = payload["global_model"]
            if round_id in self.processed_rounds or not self.sidecar.store.claim_fl_round(round_id, config.nonce, config.model_hash):
                handled.append({"round_id": round_id, "status": "duplicate"})
                continue
            request = TrainRequest(config.round_id, config.model_id, config.model_hash, payload["global_model"], config.local_steps, config.learning_rate, config.proximal_mu if config.strategy == "fedprox" else 0.0)
            result = self.harness.train(request)
            local_model = result.model
            if config.secure_aggregation:
                if not config.participants:
                    raise ValueError("secure aggregation requires participant identities")
                peers = []
                for participant_id in config.participants:
                    if participant_id == self.sidecar.identity.agent_id:
                        continue
                    peer = self.sidecar.trusted_peer(participant_id, config.project_id)
                    if peer is None:
                        raise ValueError(f"secure aggregation peer is not trusted for project: {participant_id}")
                    peers.append(peer)
                local_model = mask_model_with_identity(self.sidecar.identity, self.sidecar.identity.agent_id, peers, round_id, result.model, result.num_examples, config.mask_scale)
            update = {
                "update_id": f"{self.sidecar.identity.agent_id}:{round_id}:{uuid.uuid4().hex[:8]}",
                "node_id": self.sidecar.identity.agent_id,
                "round_id": round_id,
                "model": local_model,
                "num_examples": result.num_examples,
                "local_steps": config.local_steps,
                "train_ms": result.train_ms,
                "eval_metrics": result.metrics,
                "model_hash": config.model_hash,
                "update_hash": model_hash(local_model),
                "metadata": {"harness": self.harness.describe().name},
            }
            self.sidecar.send(self.coordinator_id, "fl.update.submit", update, project_id=config.project_id)
            self.sidecar.store.mark_fl_round(round_id, "submitted")
            self.processed_rounds.add(round_id)
            handled.append({"round_id": round_id, "status": "submitted", "update_id": update["update_id"]})
        return handled

    def apply_round_result(self, message: Dict[str, Any]) -> None:
        payload = message.get("payload", {})
        self.current_model = payload["global_model"]
        if model_hash(self.current_model) != payload["model_hash"]:
            raise ValueError("round result model hash mismatch")


class FederatedCoordinator:
    """Coordinator for real sidecar-to-sidecar FL messages."""

    def __init__(self, sidecar: Sidecar, harness: Any, participant_ids: Iterable[str], initial_model: Dict[str, List[float]], strategy: str = "fedavg", learning_rate: float = 0.2, local_steps: int = 8, proximal_mu: float = 0.01, max_byzantine: int = 0, secure_max_dropouts: int = 0, project_id: str = ""):
        self.sidecar = sidecar
        self.harness = harness
        self.owner_id = uuid.uuid4().hex
        self.lease_ttl_seconds = 30.0
        self.participant_ids = list(participant_ids)
        persisted = sidecar.store.fl_coordinator_state()
        if persisted and model_hash(persisted["model"]) == persisted["model_hash"]:
            self.global_model = clone_model(persisted["model"])
            self.round_index = int(persisted["round_index"])
        else:
            self.global_model = clone_model(initial_model)
            self.round_index = 0
        self.strategy = strategy
        self.learning_rate = learning_rate
        self.local_steps = local_steps
        self.proximal_mu = proximal_mu
        self.max_byzantine = max_byzantine
        self.secure_max_dropouts = max(0, int(secure_max_dropouts))
        self.project_id = project_id
        self.completed: Dict[str, AggregationResult] = {}

    def pending_round(self) -> Optional[RoundConfig]:
        for row in self.sidecar.store.fl_rounds(limit=100):
            if row.get("status") in {"claimed", "submitted"} and row.get("config"):
                try:
                    config = RoundConfig(**row["config"])
                except (TypeError, ValueError):
                    continue
                if config.model_hash == model_hash(self.global_model):
                    return config
        return None

    def resume_pending_round(self, timeout_seconds: float = 10.0) -> AggregationResult:
        self.acquire_lease()
        config = self.pending_round()
        if config is None:
            self.release_lease()
            raise RuntimeError("no resumable federated round")
        return self.collect_and_aggregate(config, timeout_seconds)

    def acquire_lease(self) -> None:
        if not self.sidecar.store.acquire_fl_coordinator_lease(self.owner_id, self.lease_ttl_seconds):
            raise RuntimeError("another coordinator instance currently owns the federation lease")

    def release_lease(self) -> None:
        self.sidecar.store.release_fl_coordinator_lease(self.owner_id)

    def start_round(self) -> RoundConfig:
        self.acquire_lease()
        if self.project_id:
            if not self.sidecar.store.project_active(self.project_id):
                self.release_lease()
                raise ValueError("federated project is closed or unknown")
            missing_trust = [agent_id for agent_id in self.participant_ids if self.sidecar.trusted_peer(agent_id, self.project_id) is None]
            if missing_trust:
                self.release_lease()
                raise ValueError(f"participants are not trusted for project: {missing_trust}")
        self.round_index += 1
        config = RoundConfig(
            round_id=f"round-{self.round_index:06d}",
            model_id="toy-linear-v1",
            model_hash=model_hash(self.global_model),
            strategy=self.strategy,
            local_steps=self.local_steps,
            learning_rate=self.learning_rate,
            proximal_mu=self.proximal_mu,
            min_clients=max(1, len(self.participant_ids) - self.secure_max_dropouts) if self.strategy == "secure_fedavg" else len(self.participant_ids),
            max_byzantine=self.max_byzantine,
            nonce=f"{self.sidecar.identity.agent_id}:{self.round_index}:{uuid.uuid4().hex}",
            participants=tuple(self.participant_ids),
            secure_aggregation=self.strategy == "secure_fedavg",
            participant_set_hash=participant_set_hash(self.participant_ids),
            project_id=self.project_id,
        )
        self.sidecar.store.claim_fl_round(config.round_id, config.nonce, config.model_hash, asdict(config))
        self.sidecar.store.save_fl_coordinator_state(self.global_model, self.round_index, model_hash(self.global_model))
        payload = {"round_id": config.round_id, "config": asdict(config), "global_model": self.global_model}
        for participant_id in self.participant_ids:
            self.sidecar.send(participant_id, "fl.round.request", payload, project_id=config.project_id)
        return config

    def collect_and_aggregate(self, config: RoundConfig, timeout_seconds: float = 10.0) -> AggregationResult:
        deadline = time.time() + timeout_seconds
        updates: Dict[str, NodeUpdate] = {}
        recoveries: Dict[tuple[str, str], Dict[str, List[float]]] = {}

        def consume_messages() -> None:
            for message in self.sidecar.receive(auto_ack=True):
                payload = message.get("payload", {})
                sender = message.get("sender_id")
                if message.get("type") == "fl.update.submit":
                    if payload.get("round_id") != config.round_id:
                        continue
                    if sender not in self.participant_ids or sender in updates:
                        continue
                    if payload.get("node_id") != sender or payload.get("model_hash") != config.model_hash:
                        continue
                    if payload.get("update_hash") != model_hash(payload.get("model", {})):
                        continue
                    update_id = payload.get("update_id", "")
                    if not update_id or not self.sidecar.store.record_fl_update(update_id, config.round_id, sender, payload.get("model_hash", "")):
                        continue
                    try:
                        updates[sender] = NodeUpdate(**payload)
                    except (KeyError, TypeError, ValueError):
                        continue
                elif message.get("type") == "fl.secure.recovery.submit" and config.secure_aggregation:
                    if sender not in updates or payload.get("node_id") != sender:
                        continue
                    missing_id = payload.get("missing_participant_id")
                    if missing_id not in set(self.participant_ids).difference(updates):
                        continue
                    if payload.get("participant_set_hash") != config.participant_set_hash:
                        continue
                    mask_model = payload.get("mask_model", {})
                    mask_hash = payload.get("mask_hash", "")
                    if mask_hash != model_hash(mask_model):
                        continue
                    recovery_id = payload.get("recovery_id", "")
                    if not self.sidecar.store.record_fl_recovery(recovery_id, config.round_id, sender, missing_id, mask_hash):
                        continue
                    recoveries[(sender, missing_id)] = mask_model

        while time.time() < deadline and len(updates) < len(self.participant_ids):
            consume_messages()
            if len(updates) < len(self.participant_ids):
                time.sleep(0.02)

        missing = set(self.participant_ids).difference(updates)
        if missing and config.secure_aggregation and self.secure_max_dropouts > 0 and len(missing) <= self.secure_max_dropouts and len(updates) >= config.min_clients:
            request = {"round_id": config.round_id, "missing_participants": sorted(missing), "participant_set_hash": config.participant_set_hash}
            for sender in updates:
                self.sidecar.send(sender, "fl.secure.recovery.request", request, project_id=config.project_id)
            recovery_deadline = time.time() + timeout_seconds
            expected = {(sender, missing_id) for sender in updates for missing_id in missing}
            while time.time() < recovery_deadline and not expected.issubset(recoveries):
                consume_messages()
                if not expected.issubset(recoveries):
                    time.sleep(0.02)
            if expected.issubset(recoveries):
                repaired: Dict[str, NodeUpdate] = {}
                for sender, update in updates.items():
                    model = {name: list(values) for name, values in update.model.items()}
                    for missing_id in missing:
                        mask_model = recoveries[(sender, missing_id)]
                        for name in model:
                            for index in range(len(model[name])):
                                model[name][index] -= mask_model[name][index]
                    repaired[sender] = NodeUpdate(**{**update.__dict__, "model": model, "update_hash": model_hash(model)})
                updates = repaired
                missing = set()
            else:
                missing = set(self.participant_ids).difference(updates)

        if missing or len(updates) < config.min_clients:
            self.sidecar.store.mark_fl_round(config.round_id, "aborted")
            mode = "secure aggregation recovery failed" if config.secure_aggregation else "minimum client count was not reached"
            self.release_lease()
            raise TimeoutError(f"round {config.round_id} received {len(updates)}/{config.min_clients} updates; {mode}")
        result = get_aggregator(config.strategy).aggregate(self.global_model, list(updates.values()), config)
        self.global_model = result.model
        self.sidecar.store.mark_fl_round(config.round_id, "completed")
        self.sidecar.store.save_fl_coordinator_state(self.global_model, self.round_index, model_hash(self.global_model))
        self.completed[config.round_id] = result
        payload = {"round_id": config.round_id, "model_hash": model_hash(self.global_model), "global_model": self.global_model, "accepted_update_ids": result.accepted_update_ids, "rejected_update_ids": result.rejected_update_ids}
        for participant_id in self.participant_ids:
            self.sidecar.send(participant_id, "fl.round.result", payload, project_id=config.project_id)
        self.release_lease()
        return result


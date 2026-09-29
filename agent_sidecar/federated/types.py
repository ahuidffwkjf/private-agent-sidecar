from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

ModelState = Dict[str, List[float]]


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def clone_model(model: Mapping[str, List[float]]) -> ModelState:
    return {name: [float(item) for item in values] for name, values in model.items()}


def validate_model(model: Mapping[str, List[float]]) -> None:
    if not model:
        raise ValueError("model must contain at least one parameter")
    if any(len(values) == 0 for values in model.values()):
        raise ValueError("model parameters must be non-empty")
    for values in model.values():
        for value in values:
            if not isinstance(value, (int, float)):
                raise ValueError("model parameters must be numeric")


def model_hash(model: Mapping[str, List[float]]) -> str:
    validate_model(model)
    return "sha256:" + hashlib.sha256(canonical_json(model)).hexdigest()


def model_bytes(model: Mapping[str, List[float]]) -> int:
    return len(canonical_json(model))


def participant_set_hash(participants: Sequence[str]) -> str:
    normalized = sorted({str(participant) for participant in participants})
    return "sha256:" + hashlib.sha256(canonical_json(normalized)).hexdigest()


@dataclass(frozen=True)
class RoundConfig:
    round_id: str
    model_id: str
    model_hash: str
    strategy: str = "fedavg"
    local_steps: int = 10
    learning_rate: float = 0.1
    proximal_mu: float = 0.0
    deadline_ms: int = 60_000
    min_clients: int = 1
    max_byzantine: int = 0
    nonce: str = ""
    participants: tuple[str, ...] = ()
    secure_aggregation: bool = False
    mask_scale: float = 1.0
    participant_set_hash: str = ""
    project_id: str = ""

    def __post_init__(self) -> None:
        expected = participant_set_hash(self.participants)
        if self.participant_set_hash and self.participant_set_hash != expected:
            raise ValueError("participant set hash mismatch")
        if not self.participant_set_hash:
            object.__setattr__(self, "participant_set_hash", expected)
        if self.secure_aggregation and self.strategy != "secure_fedavg":
            raise ValueError("secure aggregation requires secure_fedavg strategy")


@dataclass
class NodeUpdate:
    update_id: str
    node_id: str
    round_id: str
    model: ModelState
    num_examples: int
    local_steps: int
    train_ms: float
    eval_metrics: Dict[str, float] = field(default_factory=dict)
    model_hash: str = ""
    update_hash: str = ""
    malicious: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_model(self.model)
        if not self.model_hash:
            self.model_hash = model_hash(self.model)
        calculated_update_hash = model_hash(self.model)
        if self.update_hash and self.update_hash != calculated_update_hash:
            raise ValueError("update hash mismatch")
        self.update_hash = calculated_update_hash
        if self.num_examples <= 0:
            raise ValueError("num_examples must be positive")


@dataclass
class AggregationResult:
    strategy: str
    model: ModelState
    accepted_update_ids: List[str]
    rejected_update_ids: List[str]
    weights: Dict[str, float]
    robustness_stats: Dict[str, Any] = field(default_factory=dict)
    aggregation_ms: float = 0.0
    fallback_reason: Optional[str] = None


@dataclass(frozen=True)
class TrainRequest:
    round_id: str
    model_id: str
    model_hash: str
    global_model: ModelState
    local_steps: int
    learning_rate: float
    proximal_mu: float = 0.0


@dataclass
class TrainResult:
    model: ModelState
    num_examples: int
    train_ms: float
    metrics: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluateRequest:
    model_id: str
    model_hash: str
    model: ModelState


@dataclass
class EvaluateResult:
    metrics: Dict[str, float]
    eval_ms: float = 0.0


@dataclass(frozen=True)
class ModelArtifact:
    model_id: str
    model_hash: str
    format: str
    payload: bytes


@dataclass(frozen=True)
class UpdateArtifact:
    update_id: str
    round_id: str
    model_id: str
    model_hash: str
    format: str
    payload: bytes
    sha256: str


@dataclass(frozen=True)
class HarnessDescriptor:
    name: str
    version: str
    runtime: str = "python"
    model_family: str = "generic"
    supported_tasks: tuple[str, ...] = ()
    supported_update_formats: tuple[str, ...] = ("json-vector",)
    capabilities: Dict[str, bool] = field(default_factory=dict)

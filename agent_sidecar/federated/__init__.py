"""Lightweight federated learning primitives for the sidecar."""

from .types import (
    AggregationResult,
    EvaluateRequest,
    EvaluateResult,
    HarnessDescriptor,
    ModelArtifact,
    NodeUpdate,
    RoundConfig,
    TrainRequest,
    TrainResult,
    UpdateArtifact,
    model_hash,
    participant_set_hash,
)
from .aggregators import available_aggregators, get_aggregator
from .simulator import SimulationConfig, SimulationResult, run_simulation
from .runtime import FederatedCoordinator, FederatedNode
from .artifacts import ArtifactChunk, assemble_artifact, chunk_artifact, sha256_bytes
from .secure import mask_updates_for_secure_sum, secure_sum_equals_weighted_sum, pairwise_mask_contribution
from .privacy import PrivacyReport, gaussian_rdp_accountant

__all__ = [
    "AggregationResult", "EvaluateRequest", "EvaluateResult", "HarnessDescriptor",
    "ModelArtifact", "NodeUpdate", "RoundConfig", "TrainRequest", "TrainResult",
    "UpdateArtifact", "model_hash", "participant_set_hash", "available_aggregators", "get_aggregator",
    "SimulationConfig", "SimulationResult", "run_simulation", "FederatedCoordinator", "FederatedNode", "ArtifactChunk", "assemble_artifact", "chunk_artifact", "sha256_bytes", "mask_updates_for_secure_sum", "secure_sum_equals_weighted_sum", "pairwise_mask_contribution", "PrivacyReport", "gaussian_rdp_accountant",
]

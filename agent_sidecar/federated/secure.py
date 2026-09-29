from __future__ import annotations

import hashlib
import hmac
import random
import struct
from typing import Dict, List, Sequence

from ..crypto import Identity, PublicIdentity
from .types import ModelState, NodeUpdate, clone_model, model_hash


def _pair_rng(round_id: str, left: str, right: str, seed: int) -> random.Random:
    material = f"{round_id}:{left}:{right}:{seed}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    return random.Random(value)


def mask_updates_for_secure_sum(updates: Sequence[NodeUpdate], round_id: str, seed: int = 0, mask_scale: float = 1.0) -> List[NodeUpdate]:
    """Create pairwise-masked weighted updates whose masks cancel in the sum.

    This is a deterministic simulator for protocol testing. Real deployments
    must derive the pairwise seeds from authenticated ephemeral key agreement;
    the coordinator must never receive those pairwise secrets.
    """
    if not updates:
        raise ValueError("at least one update is required")
    names = list(updates[0].model)
    for update in updates:
        if list(update.model) != names:
            raise ValueError("all updates must have the same parameter names")
    masks: Dict[str, List[float]] = {name: [0.0] * len(updates[0].model[name]) for name in names}
    by_id = sorted(updates, key=lambda update: update.node_id)
    per_node: Dict[str, Dict[str, List[float]]] = {update.node_id: {name: [0.0] * len(update.model[name]) for name in names} for update in updates}
    for index, left in enumerate(by_id):
        for right in by_id[index + 1:]:
            rng = _pair_rng(round_id, left.node_id, right.node_id, seed)
            for name in names:
                values = [rng.gauss(0.0, mask_scale) for _ in left.model[name]]
                for position, value in enumerate(values):
                    per_node[left.node_id][name][position] += value
                    per_node[right.node_id][name][position] -= value
    masked: List[NodeUpdate] = []
    for update in updates:
        model: ModelState = {}
        for name in names:
            model[name] = [update.num_examples * value + per_node[update.node_id][name][position] for position, value in enumerate(update.model[name])]
        masked.append(NodeUpdate(
            update_id=update.update_id,
            node_id=update.node_id,
            round_id=update.round_id,
            model=model,
            num_examples=update.num_examples,
            local_steps=update.local_steps,
            train_ms=update.train_ms,
            eval_metrics=dict(update.eval_metrics),
            model_hash=update.model_hash,
            update_hash=model_hash(model),
            malicious=update.malicious,
            metadata={**update.metadata, "secure_masked": True},
        ))
    return masked


def secure_sum_equals_weighted_sum(masked: Sequence[NodeUpdate], original: Sequence[NodeUpdate]) -> bool:
    if len(masked) != len(original):
        return False
    if not masked:
        return True
    for name in masked[0].model:
        for position in range(len(masked[0].model[name])):
            left = sum(update.model[name][position] for update in masked)
            right = sum(update.num_examples * update.model[name][position] for update in original)
            if abs(left - right) > 1e-8:
                return False
    return True



def _float_mask(shared_key: bytes, round_id: str, name: str, index: int, scale: float) -> float:
    digest = hmac.new(shared_key, f"{round_id}:{name}:{index}".encode("utf-8"), hashlib.sha256).digest()
    value = struct.unpack(">Q", digest[:8])[0] / float(2**64)
    return (value - 0.5) * 2.0 * scale



def pairwise_mask_contribution(identity: Identity, own_id: str, peer: PublicIdentity, round_id: str, model: ModelState, mask_scale: float = 1.0) -> ModelState:
    """Return only this node's signed pairwise mask contribution."""
    contribution = {name: [0.0] * len(values) for name, values in model.items()}
    if peer.agent_id == own_id:
        return contribution
    salt = hashlib.sha256(round_id.encode("utf-8")).digest()
    shared = identity.derive_key(peer.encryption_key, salt=salt, info=b"private-agent-sidecar/secure-aggregation/v1")
    direction = 1.0 if own_id < peer.agent_id else -1.0
    for name, values in contribution.items():
        for index in range(len(values)):
            values[index] = direction * _float_mask(shared, round_id, name, index, mask_scale)
    return contribution

def mask_model_with_identity(identity: Identity, own_id: str, peers: Sequence[PublicIdentity], round_id: str, model: ModelState, weight: int, mask_scale: float = 1.0) -> ModelState:
    """Mask a weighted model using authenticated X25519 pairwise secrets."""
    masked = {name: [float(weight) * value for value in values] for name, values in model.items()}
    for peer in peers:
        contribution = pairwise_mask_contribution(identity, own_id, peer, round_id, model, mask_scale)
        for name, values in masked.items():
            for index in range(len(values)):
                values[index] += contribution[name][index]
    return masked

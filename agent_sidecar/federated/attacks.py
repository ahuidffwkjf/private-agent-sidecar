from __future__ import annotations

from typing import Callable

from .types import NodeUpdate, clone_model, model_hash


def label_flip(update: NodeUpdate) -> NodeUpdate:
    out = NodeUpdate(**{**update.__dict__, "metadata": {**update.metadata, "attack": "label_flip"}, "malicious": True})
    return out


def scale_update(update: NodeUpdate, scale: float = 10.0) -> NodeUpdate:
    model = {name: [value * scale for value in values] for name, values in update.model.items()}
    return NodeUpdate(**{**update.__dict__, "model": model, "update_hash": model_hash(model), "metadata": {**update.metadata, "attack": "scale", "scale": scale}, "malicious": True})


def sign_flip(update: NodeUpdate) -> NodeUpdate:
    model = {name: [-value for value in values] for name, values in update.model.items()}
    return NodeUpdate(**{**update.__dict__, "model": model, "update_hash": model_hash(model), "metadata": {**update.metadata, "attack": "sign_flip"}, "malicious": True})


def random_byzantine(update: NodeUpdate, magnitude: float = 10.0) -> NodeUpdate:
    model = {name: [magnitude if i % 2 == 0 else -magnitude for i, _ in enumerate(values)] for name, values in update.model.items()}
    return NodeUpdate(**{**update.__dict__, "model": model, "update_hash": model_hash(model), "metadata": {**update.metadata, "attack": "random_byzantine"}, "malicious": True})


def apply_attack(update: NodeUpdate, name: str, scale: float = 10.0) -> NodeUpdate:
    if name in ("", "none", None):
        return update
    attacks = {
        "label_flip": label_flip,
        "scale": lambda value: scale_update(value, scale),
        "sign_flip": sign_flip,
        "random_byzantine": random_byzantine,
    }
    if name not in attacks:
        raise ValueError(f"unknown attack: {name}")
    return attacks[name](update)

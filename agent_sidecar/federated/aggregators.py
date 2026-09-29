from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Sequence

from .types import AggregationResult, ModelState, NodeUpdate, RoundConfig, clone_model, validate_model


def _names(updates: Sequence[NodeUpdate]) -> List[str]:
    if not updates:
        raise ValueError("at least one update is required")
    names = list(updates[0].model)
    for update in updates:
        if list(update.model) != names:
            raise ValueError("all updates must have the same parameter names")
        for name in names:
            if len(update.model[name]) != len(updates[0].model[name]):
                raise ValueError("all updates must have the same parameter shapes")
    return names


def _fedavg(updates: Sequence[NodeUpdate]) -> tuple[ModelState, Dict[str, float]]:
    total = float(sum(update.num_examples for update in updates))
    if total <= 0:
        raise ValueError("total examples must be positive")
    names = _names(updates)
    model = {name: [0.0] * len(updates[0].model[name]) for name in names}
    weights: Dict[str, float] = {}
    for update in updates:
        weight = update.num_examples / total
        weights[update.update_id] = weight
        for name in names:
            for i, value in enumerate(update.model[name]):
                model[name][i] += weight * value
    return model, weights


def _distance(a: ModelState, b: ModelState) -> float:
    total = 0.0
    for name in a:
        for x, y in zip(a[name], b[name]):
            total += (x - y) ** 2
    return math.sqrt(total)


class Aggregator(ABC):
    name = "base"

    @abstractmethod
    def aggregate(self, global_model: ModelState, updates: Sequence[NodeUpdate], config: RoundConfig) -> AggregationResult:
        raise NotImplementedError


class FedAvgAggregator(Aggregator):
    name = "fedavg"

    def aggregate(self, global_model, updates, config):
        started = time.perf_counter()
        model, weights = _fedavg(updates)
        return AggregationResult(self.name, model, [u.update_id for u in updates], [], weights, aggregation_ms=(time.perf_counter() - started) * 1000)


class FedProxAggregator(FedAvgAggregator):
    name = "fedprox"


class CoordinateMedianAggregator(Aggregator):
    name = "median"

    def aggregate(self, global_model, updates, config):
        started = time.perf_counter()
        names = _names(updates)
        model: ModelState = {}
        for name in names:
            width = len(updates[0].model[name])
            model[name] = []
            for i in range(width):
                values = sorted(update.model[name][i] for update in updates)
                mid = len(values) // 2
                if len(values) % 2:
                    model[name].append(values[mid])
                else:
                    model[name].append((values[mid - 1] + values[mid]) / 2.0)
        return AggregationResult(self.name, model, [u.update_id for u in updates], [], {u.update_id: 1 / len(updates) for u in updates}, aggregation_ms=(time.perf_counter() - started) * 1000)


class TrimmedMeanAggregator(Aggregator):
    name = "trimmed_mean"

    def __init__(self, trim_ratio: float = 0.2):
        if not 0 <= trim_ratio < 0.5:
            raise ValueError("trim_ratio must be in [0, 0.5)")
        self.trim_ratio = trim_ratio

    def aggregate(self, global_model, updates, config):
        started = time.perf_counter()
        if len(updates) < 3:
            result = CoordinateMedianAggregator().aggregate(global_model, updates, config)
            result.strategy = self.name
            result.fallback_reason = "not enough updates for trimmed mean"
            result.aggregation_ms = (time.perf_counter() - started) * 1000
            return result
        names = _names(updates)
        trim = min(max(1, int(len(updates) * self.trim_ratio)), (len(updates) - 1) // 2)
        model: ModelState = {}
        for name in names:
            width = len(updates[0].model[name])
            model[name] = []
            for i in range(width):
                values = sorted(update.model[name][i] for update in updates)[trim:len(updates) - trim]
                model[name].append(sum(values) / len(values))
        return AggregationResult(self.name, model, [u.update_id for u in updates], [], {u.update_id: 1 / len(updates) for u in updates}, {"trimmed_each_side": trim}, (time.perf_counter() - started) * 1000)


class KrumAggregator(Aggregator):
    name = "krum"

    def aggregate(self, global_model, updates, config):
        started = time.perf_counter()
        n = len(updates)
        f = config.max_byzantine
        if n < 2 * f + 3:
            raise ValueError(f"Krum requires at least 2f+3 updates; got n={n}, f={f}")
        distances = {u.update_id: sorted(_distance(u.model, other.model) ** 2 for other in updates if other is not u) for u in updates}
        neighbor_count = n - f - 2
        scores = {uid: sum(values[:neighbor_count]) for uid, values in distances.items()}
        selected = min(updates, key=lambda u: scores[u.update_id])
        return AggregationResult(self.name, clone_model(selected.model), [selected.update_id], [u.update_id for u in updates if u is not selected], {selected.update_id: 1.0}, {"scores": scores, "selected": selected.update_id}, (time.perf_counter() - started) * 1000)


class SecureFedAvgAggregator(Aggregator):
    """Aggregate pairwise-masked weighted updates.

    Individual masked models are not meaningful; only their sum is divided by
    total examples. The pairwise masks must have been created by the nodes and
    cancel across the complete participant set.
    """
    name = "secure_fedavg"

    def aggregate(self, global_model, updates, config):
        started = time.perf_counter()
        names = _names(updates)
        total = float(sum(update.num_examples for update in updates))
        if total <= 0:
            raise ValueError("total examples must be positive")
        model = {name: [0.0] * len(updates[0].model[name]) for name in names}
        for update in updates:
            for name in names:
                for index, value in enumerate(update.model[name]):
                    model[name][index] += value / total
        return AggregationResult(
            self.name,
            model,
            [update.update_id for update in updates],
            [],
            {update.update_id: update.num_examples / total for update in updates},
            {"secure_aggregation": True, "individual_updates_meaningful": False},
            (time.perf_counter() - started) * 1000,
        )


_REGISTRY = {
    "fedavg": FedAvgAggregator,
    "fedprox": FedProxAggregator,
    "secure_fedavg": SecureFedAvgAggregator,
    "median": CoordinateMedianAggregator,
    "coordinate_median": CoordinateMedianAggregator,
    "trimmed_mean": TrimmedMeanAggregator,
    "krum": KrumAggregator,
}


def available_aggregators() -> List[str]:
    return sorted(_REGISTRY)


def get_aggregator(name: str) -> Aggregator:
    key = name.lower().strip()
    if key not in _REGISTRY:
        raise ValueError(f"unknown aggregation strategy: {name}; available={available_aggregators()}")
    return _REGISTRY[key]()

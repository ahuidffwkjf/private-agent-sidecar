from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class PrivacyReport:
    epsilon: Optional[float]
    delta: Optional[float]
    noise_std: float
    clip_norm: float
    steps: int
    accountant: str
    assumptions: tuple[str, ...]


def gaussian_rdp_accountant(noise_std: float, clip_norm: float, steps: int, delta: float = 1e-5, orders: Iterable[float] = (2, 4, 8, 16, 32, 64)) -> PrivacyReport:
    """Basic full-participation Gaussian RDP composition upper bound.

    This accountant assumes replace-one adjacency, per-step L2 sensitivity
    2*clip_norm, independent Gaussian noise with fixed `noise_std`, and no
    subsampling amplification. It is intentionally conservative and must not
    be used when the training protocol violates these assumptions.
    """
    if noise_std <= 0 or clip_norm <= 0 or steps <= 0:
        return PrivacyReport(None, delta, noise_std, clip_norm, steps, "basic_gaussian_rdp_upper_bound", (
            "requires positive noise_std, clip_norm and steps",
        ))
    if not 0 < delta < 1:
        raise ValueError("delta must be in (0, 1)")
    sensitivity = 2.0 * clip_norm
    best = math.inf
    for order in orders:
        if order <= 1:
            continue
        rdp = steps * (order * sensitivity * sensitivity / (2.0 * noise_std * noise_std))
        epsilon = rdp + math.log(1.0 / delta) / (order - 1.0)
        best = min(best, epsilon)
    if not math.isfinite(best):
        raise ValueError("orders must contain at least one value greater than 1")
    return PrivacyReport(best, delta, noise_std, clip_norm, steps, "basic_gaussian_rdp_upper_bound", (
        "replace-one adjacency",
        "full participation without subsampling amplification",
        "independent Gaussian noise per step",
        "fixed clipping sensitivity",
    ))

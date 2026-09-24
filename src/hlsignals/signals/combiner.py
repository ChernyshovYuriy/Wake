"""WeightedCombiner: the single home of signal weighting and the direction decision.

score = sum(w_i * component_i) / sum(w_i)   in [-1, 1]
|score| <= epsilon -> flat, else long/short by sign
"""

from __future__ import annotations

from collections.abc import Mapping

from hlsignals.core.mathx import clamp
from hlsignals.domain.models import SignalComponent, SignalDirection


class WeightedCombiner:
    def __init__(self, weights: Mapping[str, float], epsilon: float) -> None:
        if not weights:
            raise ValueError("a combiner needs at least one weight")
        if any(w < 0 for w in weights.values()):
            raise ValueError("signal weights must not be negative")
        if all(w == 0 for w in weights.values()):
            raise ValueError("all signal weights are zero")
        if not 0 <= epsilon < 1:
            raise ValueError(f"epsilon must be in [0, 1): {epsilon}")
        self.weights = dict(weights)
        self.epsilon = epsilon

    def combine(self, components: Mapping[str, SignalComponent]) -> tuple[float, SignalDirection]:
        missing = set(self.weights) - set(components)
        if missing:
            raise ValueError(f"missing signal components: {sorted(missing)}")
        total = sum(self.weights.values())
        score = clamp(
            sum(w * components[name].value for name, w in self.weights.items()) / total, -1.0, 1.0
        )
        if abs(score) <= self.epsilon:
            return score, SignalDirection.FLAT
        return score, SignalDirection.LONG if score > 0 else SignalDirection.SHORT

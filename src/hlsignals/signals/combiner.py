"""WeightedCombiner: the single home of signal weighting and the direction decision.

score = sum(w_i * component_i) / sum(w_i)   in [-1, 1]
score > epsilon -> long; score < -short_epsilon -> short; otherwise flat.
Shorts get their own (usually stricter) threshold: short_epsilon = 1 never shorts.
"""

from __future__ import annotations

from collections.abc import Mapping

from hlsignals.core.mathx import clamp
from hlsignals.domain.models import SignalComponent, SignalDirection


class WeightedCombiner:
    def __init__(self, weights: Mapping[str, float], epsilon: float, short_epsilon: float) -> None:
        if not weights:
            raise ValueError("a combiner needs at least one weight")
        if any(w < 0 for w in weights.values()):
            raise ValueError("signal weights must not be negative")
        if all(w == 0 for w in weights.values()):
            raise ValueError("all signal weights are zero")
        if not 0 <= epsilon < 1:
            raise ValueError(f"epsilon must be in [0, 1): {epsilon}")
        if not 0 <= short_epsilon <= 1:
            raise ValueError(f"short_epsilon must be in [0, 1]: {short_epsilon}")
        self.weights = dict(weights)
        self.epsilon = epsilon
        self.short_epsilon = short_epsilon

    def combine(self, components: Mapping[str, SignalComponent]) -> tuple[float, SignalDirection]:
        missing = set(self.weights) - set(components)
        if missing:
            raise ValueError(f"missing signal components: {sorted(missing)}")
        total = sum(self.weights.values())
        score = clamp(
            sum(w * components[name].value for name, w in self.weights.items()) / total, -1.0, 1.0
        )
        if score > self.epsilon:
            return score, SignalDirection.LONG
        if score < -self.short_epsilon:
            return score, SignalDirection.SHORT
        return score, SignalDirection.FLAT

    def explain(self, score: float, direction: SignalDirection) -> str:
        """The threshold comparison behind ``combine``'s decision, in words."""
        if direction is SignalDirection.LONG:
            return f"score {score:+.3f} > epsilon {self.epsilon}"
        if direction is SignalDirection.SHORT:
            return f"score {score:+.3f} < -short_epsilon {self.short_epsilon}"
        return f"score {score:+.3f} within [-{self.short_epsilon}, +{self.epsilon}]"

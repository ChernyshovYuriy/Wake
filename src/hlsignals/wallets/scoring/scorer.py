"""WalletScorer (Composite of features): the single home of the trust formula.

    trust = clamp(sum(w_i * f_i) / sum(w_i)) * confidence * decay
    confidence = shrinkage(n_scored_trips, k)        -- small samples count less
    decay = half_life_decay(days since last US-stock fill, half_life)

Features that return None (e.g. no external score) are left out of both sums.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hlsignals.core.mathx import clamp, half_life_decay, safe_div, shrinkage
from hlsignals.domain.models import FeatureValue, ScoredWallet
from hlsignals.wallets.scoring.features import WalletFeature
from hlsignals.wallets.scoring.slice import EquitySlice


@dataclass(frozen=True, slots=True)
class WeightedFeature:
    feature: WalletFeature
    weight: float


class WalletScorer:
    def __init__(
        self, features: Sequence[WeightedFeature], shrinkage_k: float, half_life_days: float
    ) -> None:
        if not features:
            raise ValueError("a wallet scorer needs at least one feature")
        names = [wf.feature.name for wf in features]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate wallet features: {names}")
        if any(wf.weight < 0 for wf in features):
            raise ValueError("feature weights must not be negative")
        if all(wf.weight == 0 for wf in features):
            raise ValueError("all feature weights are zero")
        if shrinkage_k <= 0:
            raise ValueError(f"shrinkage_k must be positive: {shrinkage_k}")
        if half_life_days <= 0:
            raise ValueError(f"half_life_days must be positive: {half_life_days}")
        self._features = tuple(features)
        self._k = shrinkage_k
        self._half_life = half_life_days

    def score(self, wallet: EquitySlice) -> ScoredWallet:
        computed: dict[str, FeatureValue] = {}
        weighted_sum = weight_total = 0.0
        for wf in self._features:
            value = wf.feature.compute(wallet)
            if value is None:
                continue
            computed[wf.feature.name] = FeatureValue(
                value.value, {**value.evidence, "weight": wf.weight}
            )
            weighted_sum += wf.weight * value.value
            weight_total += wf.weight
        base = clamp(safe_div(weighted_sum, weight_total, default=0.0), 0.0, 1.0)
        confidence = shrinkage(wallet.n_scored_trips, self._k)
        age = wallet.days_since_last_fill
        decay = 0.0 if age is None else half_life_decay(age, self._half_life)
        return ScoredWallet(
            address=wallet.address,
            trust=clamp(base * confidence * decay, 0.0, 1.0),
            confidence=confidence,
            decay=decay,
            features=computed,
            n_closed_lots=wallet.n_scored_trips,
            track_record_days=wallet.track_record_days,
        )

"""Wallet features (Strategy): each maps an EquitySlice to a FeatureValue in [0, 1].

Every value carries the raw inputs that produced it. With no scored round trips a feature
scores 0 (no evidence is not good evidence). PriorScoreFeature returns None when the wallet
has no external score, which makes the scorer skip it and renormalize the weights.
"""

from __future__ import annotations

from typing import Protocol

from hlsignals.core.clock import MS_PER_DAY
from hlsignals.core.mathx import clamp, wilson_lower_bound
from hlsignals.domain.models import FeatureValue
from hlsignals.wallets.scoring.slice import EquitySlice


class WalletFeature(Protocol):
    @property
    def name(self) -> str: ...

    def compute(self, wallet: EquitySlice) -> FeatureValue | None: ...


def _require_positive(name: str, value: float) -> None:
    if not value > 0:
        raise ValueError(f"{name} must be positive: {value}")


def _no_trips() -> FeatureValue:
    return FeatureValue(0.0, {"n_trips": 0})


class HitRateFeature:
    """Wilson lower bound of the win rate: a few lucky trips cannot score high."""

    name = "hit_rate"

    def __init__(self, z: float) -> None:
        _require_positive("hit-rate z", z)
        self.z = z

    def compute(self, wallet: EquitySlice) -> FeatureValue:
        n, wins = wallet.n_scored_trips, wallet.wins
        if n == 0:
            return _no_trips()
        evidence = {"wins": wins, "n_trips": n, "z": self.z, "raw_hit_rate": wins / n}
        return FeatureValue(wilson_lower_bound(wins, n, self.z), evidence)


class DrawdownFeature:
    """1 - (max drawdown of cumulative trip returns / tolerated drawdown), floored at 0."""

    name = "drawdown"

    def __init__(self, max_tolerated_dd: float) -> None:
        _require_positive("tolerated drawdown", max_tolerated_dd)
        self.max_tolerated_dd = max_tolerated_dd

    def compute(self, wallet: EquitySlice) -> FeatureValue:
        trips = wallet.scored_trips
        if not trips:
            return _no_trips()
        cumulative = peak = max_dd = 0.0
        for trip in trips:
            cumulative += trip.return_frac or 0.0
            peak = max(peak, cumulative)
            max_dd = max(max_dd, peak - cumulative)
        value = 1.0 - clamp(max_dd / self.max_tolerated_dd, 0.0, 1.0)
        evidence = {
            "n_trips": len(trips),
            "max_drawdown": max_dd,
            "cumulative_return": cumulative,
            "tolerated_drawdown": self.max_tolerated_dd,
        }
        return FeatureValue(value, evidence)


class ConsistencyFeature:
    """Share of calendar periods (by trip close time) with positive realized PnL."""

    name = "consistency"

    def __init__(self, period_days: float) -> None:
        _require_positive("consistency period", period_days)
        self.period_ms = period_days * MS_PER_DAY
        self.period_days = period_days

    def compute(self, wallet: EquitySlice) -> FeatureValue:
        trips = wallet.scored_trips
        if not trips:
            return _no_trips()
        by_period: dict[int, float] = {}
        for trip in trips:
            bucket = int(trip.close_ms // self.period_ms)
            by_period[bucket] = by_period.get(bucket, 0.0) + float(trip.realized_pnl or 0)
        positive = sum(1 for pnl in by_period.values() if pnl > 0)
        evidence = {
            "n_trips": len(trips),
            "periods": len(by_period),
            "positive_periods": positive,
            "negative_or_flat_periods": len(by_period) - positive,
            "period_days": self.period_days,
        }
        return FeatureValue(positive / len(by_period), evidence)


class HorizonFitFeature:
    """How well the median hold matches the swing horizon: min(r, 1/r), r = hold/horizon."""

    name = "horizon_fit"

    def __init__(self, horizon_days: float) -> None:
        _require_positive("swing horizon", horizon_days)
        self.horizon_days = horizon_days

    def compute(self, wallet: EquitySlice) -> FeatureValue:
        median = wallet.median_hold_days
        if median is None:
            return _no_trips()
        ratio = median / self.horizon_days  # finite and >= 0: holds are finite, horizon > 0
        value = min(ratio, 1.0 / ratio) if ratio > 0 else 0.0
        evidence = {
            "n_trips": wallet.n_scored_trips,
            "median_hold_days": median,
            "horizon_days": self.horizon_days,
        }
        return FeatureValue(value, evidence)


class PriorScoreFeature:
    """An external score (e.g. a Copy Score) scaled to [0, 1]; skipped when absent."""

    name = "prior_score"

    def __init__(self, scale: float) -> None:
        _require_positive("prior score scale", scale)
        self.scale = scale

    def compute(self, wallet: EquitySlice) -> FeatureValue | None:
        if wallet.raw_score is None:
            return None
        value = clamp(wallet.raw_score / self.scale, 0.0, 1.0)
        return FeatureValue(value, {"raw_score": wallet.raw_score, "scale": self.scale})

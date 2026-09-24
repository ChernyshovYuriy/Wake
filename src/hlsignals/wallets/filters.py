"""Wallet filters (Chain of Responsibility over EquitySlice). Thresholds are inclusive.

Order is set in config; each rejection records why, for the ``vet`` report.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hlsignals.core.chain import Filter, FilterChain, Verdict
from hlsignals.core.clock import MS_PER_HOUR
from hlsignals.domain.candles import price_at
from hlsignals.domain.models import PositionSide
from hlsignals.wallets.scoring.slice import EquitySlice


def _require_threshold(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(f"invalid threshold: {message}")


@dataclass(frozen=True, slots=True)
class MinEquitySampleFilter:
    min_round_trips: int
    name: str = "min_sample"

    def __post_init__(self) -> None:
        _require_threshold(self.min_round_trips >= 0, f"min_round_trips={self.min_round_trips}")

    def apply(self, item: EquitySlice) -> Verdict:
        n = item.n_scored_trips
        if n >= self.min_round_trips:
            return Verdict.accept()
        return Verdict.reject(f"{n} scored round trips < {self.min_round_trips}")


@dataclass(frozen=True, slots=True)
class MakerProfileFilter:
    """Rejects market-maker / HFT profiles: passive fills, very many fills, tiny holds."""

    min_taker_ratio: float
    max_fills_per_day: float
    min_median_hold_days: float
    name: str = "maker_profile"

    def __post_init__(self) -> None:
        _require_threshold(0 <= self.min_taker_ratio <= 1, f"taker={self.min_taker_ratio}")
        _require_threshold(self.max_fills_per_day > 0, f"fills/day={self.max_fills_per_day}")
        _require_threshold(self.min_median_hold_days >= 0, "min_median_hold_days")

    def apply(self, item: EquitySlice) -> Verdict:
        problems = []
        taker = item.taker_ratio
        if taker is not None and taker < self.min_taker_ratio:
            problems.append(f"taker ratio {taker:.2f} < {self.min_taker_ratio:.2f}")
        per_day = item.fills_per_active_day
        if per_day > self.max_fills_per_day:
            problems.append(f"{per_day:.0f} fills/day > {self.max_fills_per_day:.0f}")
        hold = item.median_hold_days
        if hold is not None and hold < self.min_median_hold_days:
            problems.append(
                f"median hold {hold * 24:.2f} h < {self.min_median_hold_days * 24:.2f} h"
            )
        return Verdict.reject("; ".join(problems)) if problems else Verdict.accept()


@dataclass(frozen=True, slots=True)
class InactivityFilter:
    max_days_since_last_fill: float
    name: str = "inactivity"

    def __post_init__(self) -> None:
        _require_threshold(self.max_days_since_last_fill >= 0, "max_days_since_last_fill")

    def apply(self, item: EquitySlice) -> Verdict:
        idle = item.days_since_last_fill
        if idle is None:
            return Verdict.reject("no US-stock fills")
        if idle <= self.max_days_since_last_fill:
            return Verdict.accept()
        return Verdict.reject(
            f"last US-stock fill {idle:.1f} days ago > {self.max_days_since_last_fill} days"
        )


@dataclass(frozen=True, slots=True)
class ReversalBaitFilter:
    """Flags wallets whose quick trips reliably exit just before the price reverses.

    Event: a scored round trip held at most ``window_hours``. It counts as a reversal when
    the price ``window_hours`` after the exit has moved against the trip's side by at
    least ``min_move`` (a fraction), i.e. the wallet got out right before followers would
    have lost. Events without candles, or whose window ends after ``as_of``, are not
    evaluable. Rejects when at least ``min_events`` events are evaluable and the reversal
    rate exceeds ``max_reversal_rate``.
    """

    window_hours: float
    min_events: int
    max_reversal_rate: float
    min_move: float
    name: str = "reversal_bait"

    def __post_init__(self) -> None:
        if not (
            self.window_hours > 0
            and self.min_events >= 1
            and 0 <= self.max_reversal_rate <= 1
            and self.min_move >= 0
        ):
            raise ValueError(f"invalid reversal-bait parameters: {self}")

    def apply(self, item: EquitySlice) -> Verdict:
        window_ms = int(self.window_hours * MS_PER_HOUR)
        evaluable = reversals = 0
        for trip in item.scored_trips:
            held = trip.holding_ms
            after_ms = trip.close_ms + window_ms
            candles = item.candles.get(trip.symbol, ())
            if held is None or held > window_ms or after_ms > item.as_of_ms:
                continue
            ref, after = price_at(candles, trip.close_ms), price_at(candles, after_ms)
            if ref is None or after is None:
                continue
            evaluable += 1
            direction = 1 if trip.side is PositionSide.LONG else -1
            if direction * (after - ref) / ref <= -self.min_move:
                reversals += 1
        if evaluable < self.min_events:
            return Verdict.accept()
        rate = reversals / evaluable
        if rate <= self.max_reversal_rate:
            return Verdict.accept()
        return Verdict.reject(
            f"{reversals}/{evaluable} quick trips reversed after exit "
            f"({rate:.0%} > {self.max_reversal_rate:.0%})"
        )


def wallet_filter_chain(filters: Sequence[Filter[EquitySlice]]) -> FilterChain[EquitySlice]:
    return FilterChain[EquitySlice](filters)

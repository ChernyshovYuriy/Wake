"""EquitySlice: a wallet's history restricted to US-stock symbols up to ``as_of``.

The single home for "which of this wallet's activity counts": only fills on the
configured equity symbols and at or before ``as_of`` are kept, so crypto trading never
affects a wallet's score. Also the single home for the wallet statistics that filters
and features share.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from hlsignals.core.clock import MS_PER_DAY, ms_to_days
from hlsignals.domain.lots import LotBook, RoundTrip
from hlsignals.domain.models import Candle, Fill, Position
from hlsignals.domain.symbols import Symbol


def fills_per_active_day(fills: Sequence[Fill]) -> float:
    """Fills divided by the number of distinct UTC days that had any fill."""
    days = {f.time_ms // MS_PER_DAY for f in fills}
    return len(fills) / len(days) if days else 0.0


def equity_fills(fills: Iterable[Fill], equities: frozenset[Symbol]) -> list[Fill]:
    """The fills on equity symbols, in their original order: the single place a wallet's
    history is restricted to the universe (IMPLEMENTATION_PLAN.md §5)."""
    return [f for f in fills if f.symbol in equities]


@dataclass(frozen=True, slots=True)
class EquitySlice:
    address: str
    as_of_ms: int
    fills: tuple[Fill, ...]
    round_trips: tuple[RoundTrip, ...]
    positions: tuple[Position, ...]
    candles: Mapping[Symbol, Sequence[Candle]] = field(default_factory=dict)
    raw_score: float | None = None
    truncated: bool = False  # the API history cap was hit: older activity is missing
    net_sizes: Mapping[Symbol, Decimal] = field(default_factory=dict)  # position at as_of

    @classmethod
    def from_history(
        cls,
        address: str,
        fills: Iterable[Fill],
        *,
        positions: Iterable[Position],
        equities: frozenset[Symbol],
        as_of_ms: int,
        candles: Mapping[Symbol, Sequence[Candle]] | None = None,
        raw_score: float | None = None,
        truncated: bool = False,
    ) -> EquitySlice:
        kept = tuple(f for f in equity_fills(fills, equities) if f.time_ms <= as_of_ms)
        book = LotBook()
        book.add_all(kept)
        return cls(
            address=address,
            as_of_ms=as_of_ms,
            fills=kept,
            round_trips=book.round_trips,
            positions=tuple(p for p in positions if p.symbol in equities),
            candles=dict(candles or {}),
            raw_score=raw_score,
            truncated=truncated,
            net_sizes={s: book.position(s) for s in sorted(book.symbols)},
        )

    @property
    def scored_trips(self) -> tuple[RoundTrip, ...]:
        """Round trips with known PnL (orphans excluded), in close order."""
        return tuple(t for t in self.round_trips if not t.is_orphan)

    @property
    def n_scored_trips(self) -> int:
        return len(self.scored_trips)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.scored_trips if (t.realized_pnl or 0) > 0)

    @property
    def median_hold_days(self) -> float | None:
        holds = [ms_to_days(t.holding_ms) for t in self.scored_trips if t.holding_ms is not None]
        return statistics.median(holds) if holds else None

    @property
    def taker_ratio(self) -> float | None:
        """Share of fills that crossed the spread (taker); market makers sit near 0."""
        if not self.fills:
            return None
        return sum(1 for f in self.fills if f.crossed) / len(self.fills)

    @property
    def fills_per_active_day(self) -> float:
        return fills_per_active_day(self.fills)

    @property
    def last_fill_ms(self) -> int | None:
        return self.fills[-1].time_ms if self.fills else None

    @property
    def days_since_last_fill(self) -> float | None:
        last = self.last_fill_ms
        return None if last is None else ms_to_days(self.as_of_ms - last)

    @property
    def track_record_days(self) -> float:
        if not self.fills:
            return 0.0
        return ms_to_days(self.fills[-1].time_ms - self.fills[0].time_ms)

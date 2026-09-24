"""Market filters (Chain of Responsibility over MarketCtx). Thresholds are inclusive."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hlsignals.core.chain import Filter, FilterChain, Verdict
from hlsignals.domain.models import MarketCtx


def _require_non_negative(value: float) -> None:
    if value < 0:
        raise ValueError(f"threshold must be non-negative: {value}")


@dataclass(frozen=True, slots=True)
class DelistedFilter:
    name: str = "delisted"

    def apply(self, item: MarketCtx) -> Verdict:
        return Verdict.reject("market is delisted") if item.is_delisted else Verdict.accept()


@dataclass(frozen=True, slots=True)
class MinDayVolumeFilter:
    min_usd: float
    name: str = "min_day_volume"

    def __post_init__(self) -> None:
        _require_non_negative(self.min_usd)

    def apply(self, item: MarketCtx) -> Verdict:
        if item.day_ntl_vlm >= self.min_usd:
            return Verdict.accept()
        return Verdict.reject(f"24h volume ${item.day_ntl_vlm:,.0f} < ${self.min_usd:,.0f}")


@dataclass(frozen=True, slots=True)
class MinOpenInterestFilter:
    min_usd: float
    name: str = "min_open_interest"

    def __post_init__(self) -> None:
        _require_non_negative(self.min_usd)

    def apply(self, item: MarketCtx) -> Verdict:
        if item.oi_usd >= self.min_usd:
            return Verdict.accept()
        return Verdict.reject(f"open interest ${item.oi_usd:,.0f} < ${self.min_usd:,.0f}")


@dataclass(frozen=True, slots=True)
class WatchlistFilter:
    coins: frozenset[str]
    name: str = "watchlist"

    def apply(self, item: MarketCtx) -> Verdict:
        if item.symbol.coin in self.coins:
            return Verdict.accept()
        return Verdict.reject(f"{item.symbol.coin} is not on the watchlist")


def market_filter_chain(filters: Sequence[Filter[MarketCtx]]) -> FilterChain[MarketCtx]:
    return FilterChain[MarketCtx](filters)

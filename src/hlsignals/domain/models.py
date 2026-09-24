"""Frozen domain models. Adapters (infra) build these from raw API JSON.

Accounting quantities from the API (fill prices/sizes/PnL, position sizes) are Decimal so
lot matching is exact; analytic quantities (candles, market context, scores) are float.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from hlsignals.domain.address import require_address
from hlsignals.domain.symbols import Symbol

EvidenceValue = float | int | str | bool | None


class Side(StrEnum):
    """Order side as reported by the API: ``B`` = bid/buy, ``A`` = ask/sell."""

    BUY = "B"
    SELL = "A"


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _require_unit(name: str, value: float) -> None:
    _require(math.isfinite(value) and 0.0 <= value <= 1.0, f"{name} must be in [0, 1]: {value}")


def _require_aware(name: str, value: datetime) -> None:
    _require(value.utcoffset() is not None, f"{name} must be timezone-aware: {value!r}")


@dataclass(frozen=True, slots=True)
class Fill:
    wallet: str
    symbol: Symbol
    px: Decimal
    sz: Decimal
    side: Side
    dir: str
    time_ms: int
    tid: int
    hash: str
    crossed: bool  # True = taker
    closed_pnl: Decimal  # gross of fees (docs/api-notes.md §3)
    fee: Decimal  # negative = maker rebate
    start_position: Decimal  # signed position before this fill
    liquidated_user: str | None = None

    def __post_init__(self) -> None:
        require_address(self.wallet)
        _require(self.px > 0, f"fill px must be positive: {self.px}")
        _require(self.sz >= 0, f"fill sz must be non-negative: {self.sz}")
        _require(self.time_ms >= 0, f"fill time must be non-negative: {self.time_ms}")

    @property
    def notional(self) -> Decimal:
        return self.px * self.sz

    @property
    def dedupe_key(self) -> tuple[int, str]:
        return (self.tid, self.hash)


@dataclass(frozen=True, slots=True)
class Position:
    wallet: str
    symbol: Symbol
    size: Decimal  # signed: + long, - short
    entry_px: Decimal
    position_value: Decimal
    unrealized_pnl: Decimal
    time_ms: int

    def __post_init__(self) -> None:
        require_address(self.wallet)
        _require(self.entry_px > 0, f"entry px must be positive: {self.entry_px}")

    @property
    def side(self) -> PositionSide | None:
        if self.size == 0:
            return None
        return PositionSide.LONG if self.size > 0 else PositionSide.SHORT


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: Symbol
    interval: str
    open_ms: int
    close_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    n_trades: int

    def __post_init__(self) -> None:
        _require(self.close_ms >= self.open_ms, "candle closes before it opens")
        _require(0 < self.low <= self.high, f"invalid candle range {self.low}..{self.high}")
        for px in (self.open, self.close):
            _require(self.low <= px <= self.high, f"open/close {px} outside low..high")
        _require(self.volume >= 0 and self.n_trades >= 0, "negative candle volume")


@dataclass(frozen=True, slots=True)
class MarketCtx:
    symbol: Symbol
    mark_px: float
    oracle_px: float
    prev_day_px: float
    mid_px: float | None
    open_interest: float  # base units (contracts), as the API reports it
    day_ntl_vlm: float  # USD
    funding: float
    is_delisted: bool

    def __post_init__(self) -> None:
        _require(self.mark_px > 0 and self.oracle_px > 0, "market prices must be positive")
        _require(self.open_interest >= 0, f"negative open interest: {self.open_interest}")
        _require(self.day_ntl_vlm >= 0, f"negative volume: {self.day_ntl_vlm}")

    @property
    def oi_usd(self) -> float:
        return self.open_interest * self.mark_px


@dataclass(frozen=True, slots=True)
class WalletRecord:
    address: str
    source: str
    raw_score: float | None
    raw_metric: str | None
    as_of: datetime

    def __post_init__(self) -> None:
        require_address(self.address)
        _require_aware("as_of", self.as_of)
        if self.raw_score is not None:
            _require(
                math.isfinite(self.raw_score) and self.raw_score >= 0,
                f"raw_score must be a non-negative number: {self.raw_score}",
            )


@dataclass(frozen=True, slots=True)
class FeatureValue:
    value: float  # normalised to [0, 1]
    evidence: Mapping[str, EvidenceValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_unit("feature value", self.value)
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))


@dataclass(frozen=True, slots=True)
class ScoredWallet:
    address: str
    trust: float
    confidence: float
    decay: float
    features: Mapping[str, FeatureValue]
    n_closed_lots: int
    track_record_days: float

    def __post_init__(self) -> None:
        require_address(self.address)
        for name in ("trust", "confidence", "decay"):
            _require_unit(name, getattr(self, name))
        _require(self.n_closed_lots >= 0, "negative lot count")
        _require(self.track_record_days >= 0, "negative track record")
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))

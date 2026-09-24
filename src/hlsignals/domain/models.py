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

from hlsignals.core.clock import require_aware
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


@dataclass(frozen=True, slots=True)
class Dex:
    """A HIP-3 builder dex (``perpDexs`` entry)."""

    name: str
    full_name: str


@dataclass(frozen=True, slots=True)
class BookLevel:
    px: Decimal
    sz: Decimal
    n_orders: int

    def __post_init__(self) -> None:
        _require(self.px > 0 and self.sz >= 0, f"invalid book level {self.px} x {self.sz}")


@dataclass(frozen=True, slots=True)
class L2Book:
    symbol: Symbol
    time_ms: int
    bids: tuple[BookLevel, ...]  # best first (descending px)
    asks: tuple[BookLevel, ...]  # best first (ascending px)

    def __post_init__(self) -> None:
        _require(
            all(a.px > b.px for a, b in zip(self.bids, self.bids[1:], strict=False))
            and all(a.px < b.px for a, b in zip(self.asks, self.asks[1:], strict=False)),
            "book levels out of order",
        )


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
class TapeTrade:
    """One public trade (``recentTrades`` / WS ``trades``): both counterparties known."""

    symbol: Symbol
    side: Side  # aggressor side
    px: Decimal
    sz: Decimal
    time_ms: int
    tid: int
    buyer: str
    seller: str

    def __post_init__(self) -> None:
        require_address(self.buyer)
        require_address(self.seller)
        _require(self.px > 0, f"trade px must be positive: {self.px}")
        _require(self.sz >= 0, f"trade sz must be non-negative: {self.sz}")


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
        require_aware(self.as_of)
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


class SignalDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class SignalStatus(StrEnum):
    SCORED = "scored"
    INSUFFICIENT = "insufficient"  # not enough independent trusted wallets


class SignalFlag(StrEnum):
    THIN_VOLUME = "thin_volume"
    WEAK_SAMPLE = "weak_sample"
    STALE_OVERNIGHT_REF = "stale_overnight_ref"
    CASH_SESSION_OPEN = "cash_session_open"


@dataclass(frozen=True, slots=True)
class SignalComponent:
    """One signal feature's signed contribution: + bullish, - bearish, in [-1, 1]."""

    value: float
    evidence: Mapping[str, EvidenceValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require(
            math.isfinite(self.value) and -1.0 <= self.value <= 1.0,
            f"signal component must be in [-1, 1]: {self.value}",
        )
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))


@dataclass(frozen=True, slots=True)
class TickerSignal:
    symbol: Symbol
    status: SignalStatus
    direction: SignalDirection | None  # None when INSUFFICIENT
    score: float | None  # combined, in [-1, 1]; None when INSUFFICIENT
    components: Mapping[str, SignalComponent]
    n_wallets: int  # distinct trusted wallets holding or trading the symbol
    reason: str  # why INSUFFICIENT, or how the direction was decided
    flags: frozenset[SignalFlag]
    market: MarketCtx

    def __post_init__(self) -> None:
        scored = self.status is SignalStatus.SCORED
        _require(
            scored == (self.score is not None) == (self.direction is not None),
            "score and direction are required exactly when the signal is scored",
        )
        if self.score is not None:
            _require(-1.0 <= self.score <= 1.0, f"score must be in [-1, 1]: {self.score}")
        object.__setattr__(self, "components", MappingProxyType(dict(self.components)))


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """Everything a reader needs to judge a report: what went in, what was dropped, why."""

    sources_used: tuple[str, ...]
    sources_failed: tuple[str, ...]
    source_messages: tuple[str, ...]  # e.g. "nansen: disabled: NANSEN_API_KEY is not set"
    wallets_considered: int
    wallets_accepted: int
    wallets_rejected: Mapping[str, int]  # filter name -> count
    wallets_truncated: int  # wallets whose history hit the API cap
    universe_discovered: int
    universe_after_filters: int
    markets_rejected: Mapping[str, int]  # filter name -> count
    unclassified_symbols: tuple[str, ...]
    dex_failures: Mapping[str, str]
    notes: tuple[str, ...] = ()  # anything else a reader must know (e.g. missing positions)

    def __post_init__(self) -> None:
        _require(
            0 <= self.wallets_accepted <= self.wallets_considered,
            "accepted wallets cannot exceed considered wallets",
        )
        _require(
            0 <= self.universe_after_filters <= self.universe_discovered,
            "filtered universe cannot exceed the discovered universe",
        )
        for name in ("wallets_rejected", "markets_rejected", "dex_failures"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


@dataclass(frozen=True, slots=True)
class SignalReport:
    as_of: datetime
    signals: tuple[TickerSignal, ...]  # ranked
    diagnostics: Diagnostics
    wallets: tuple[ScoredWallet, ...] = ()  # accepted wallets, by trust descending

    def __post_init__(self) -> None:
        require_aware(self.as_of)

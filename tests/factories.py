"""Builders for domain objects. All test setup goes through these (CLAUDE.md rule 2)."""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from hlsignals.domain.direction import sign_of
from hlsignals.domain.models import (
    Candle,
    FeatureValue,
    Fill,
    MarketCtx,
    Position,
    ScoredWallet,
    Side,
    TapeTrade,
    WalletRecord,
)
from hlsignals.domain.symbols import Symbol

WALLET = "0x" + "a1" * 20
OTHER_WALLET = "0x" + "b2" * 20
NVDA = Symbol("xyz", "NVDA")
AAPL = Symbol("xyz", "AAPL")
BTC = Symbol(None, "BTC")
T0_MS = 1_790_000_000_000
AS_OF = datetime(2026, 9, 24, tzinfo=UTC)
HOUR_MS = 3_600_000

_tids = itertools.count(1)


def D(value: Number) -> Decimal:  # noqa: N802 - reads like the Decimal it builds
    return Decimal(str(value))


def make_fill(**overrides: Any) -> Fill:
    """A fill whose ``side`` follows its ``dir`` unless overridden."""
    direction = overrides.get("dir", "Open Long")
    tid = next(_tids)
    defaults: dict[str, Any] = {
        "wallet": WALLET,
        "symbol": NVDA,
        "px": D(100),
        "sz": D(1),
        "side": Side.BUY if sign_of(direction) > 0 else Side.SELL,
        "dir": direction,
        "time_ms": T0_MS,
        "tid": tid,
        "hash": f"0x{tid:064x}",
        "crossed": True,
        "closed_pnl": D(0),
        "fee": D(0),
        "start_position": D(0),
    }
    return Fill(**{**defaults, **overrides})


Number = float | str | Decimal
Step = tuple[str, Number, Number]  # (dir, sz, px)


def make_fills(
    steps: Iterable[Step],
    *,
    symbol: Symbol = NVDA,
    start_position: Number = 0,
    t0_ms: int = T0_MS,
    step_ms: int = HOUR_MS,
    **overrides: Any,
) -> list[Fill]:
    """A consistent fill sequence: ``start_position`` chains through every step."""
    fills: list[Fill] = []
    position = D(start_position)
    for i, (direction, sz, px) in enumerate(steps):
        fill = make_fill(
            dir=direction,
            sz=D(sz),
            px=D(px),
            symbol=symbol,
            time_ms=t0_ms + i * step_ms,
            start_position=position,
            **overrides,
        )
        position += sign_of(direction) * fill.sz
        fills.append(fill)
    return fills


def mirror(fill: Fill) -> Fill:
    """The same fill with every direction reversed (long <-> short)."""
    swapped = {
        "Open Long": "Open Short",
        "Open Short": "Open Long",
        "Close Long": "Close Short",
        "Close Short": "Close Long",
        "Long > Short": "Short > Long",
        "Short > Long": "Long > Short",
    }[fill.dir]
    return replace(
        fill,
        dir=swapped,
        side=Side.SELL if fill.side is Side.BUY else Side.BUY,
        start_position=-fill.start_position,
        closed_pnl=-fill.closed_pnl,
    )


def make_position(**overrides: Any) -> Position:
    defaults: dict[str, Any] = {
        "wallet": WALLET,
        "symbol": NVDA,
        "size": D(10),
        "entry_px": D(100),
        "position_value": D(1000),
        "unrealized_pnl": D(0),
        "time_ms": T0_MS,
    }
    return Position(**{**defaults, **overrides})


def make_candle_series(
    closes: Sequence[float],
    *,
    symbol: Symbol = NVDA,
    interval: str = "1h",
    t0_ms: int = T0_MS,
    step_ms: int = HOUR_MS,
) -> list[Candle]:
    """Candles whose open is the previous close; high/low bracket open and close."""
    candles = []
    prev = closes[0] if closes else 0.0
    for i, close in enumerate(closes):
        open_ms = t0_ms + i * step_ms
        candles.append(
            Candle(
                symbol=symbol,
                interval=interval,
                open_ms=open_ms,
                close_ms=open_ms + step_ms - 1,
                open=prev,
                high=max(prev, close),
                low=min(prev, close),
                close=close,
                volume=1.0,
                n_trades=1,
            )
        )
        prev = close
    return candles


def make_market_ctx(**overrides: Any) -> MarketCtx:
    defaults: dict[str, Any] = {
        "symbol": NVDA,
        "mark_px": 100.0,
        "oracle_px": 100.0,
        "prev_day_px": 99.0,
        "mid_px": 100.0,
        "open_interest": 10_000.0,
        "day_ntl_vlm": 5_000_000.0,
        "funding": 0.0,
        "is_delisted": False,
    }
    return MarketCtx(**{**defaults, **overrides})


def make_wallet_record(**overrides: Any) -> WalletRecord:
    defaults: dict[str, Any] = {
        "address": WALLET,
        "source": "curated",
        "raw_score": None,
        "raw_metric": None,
        "as_of": AS_OF,
    }
    return WalletRecord(**{**defaults, **overrides})


def make_scored_wallet(
    features: Mapping[str, FeatureValue] | None = None, **overrides: Any
) -> ScoredWallet:
    defaults: dict[str, Any] = {
        "address": WALLET,
        "trust": 0.5,
        "confidence": 0.5,
        "decay": 1.0,
        "features": features if features is not None else {"hit_rate": FeatureValue(0.5)},
        "n_closed_lots": 10,
        "track_record_days": 30.0,
    }
    return ScoredWallet(**{**defaults, **overrides})


def make_tape_trade(**overrides: Any) -> TapeTrade:
    tid = next(_tids)
    defaults: dict[str, Any] = {
        "symbol": NVDA,
        "side": Side.BUY,
        "px": D(100),
        "sz": D(1),
        "time_ms": T0_MS,
        "tid": tid,
        "buyer": WALLET,
        "seller": OTHER_WALLET,
    }
    return TapeTrade(**{**defaults, **overrides})

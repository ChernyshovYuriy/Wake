"""Validation edges of the domain value objects: the boundary value that is still valid, and the
exact message of each rejection (AUDIT.md Phase 6: these boundaries were unpinned)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from datetime import date

import pytest

from hlsignals.core.errors import AdapterError, NonPerpFillError
from hlsignals.domain.address import require_address
from hlsignals.domain.direction import sign_of
from hlsignals.domain.models import (
    BookLevel,
    DailyBar,
    L2Book,
    SignalDirection,
    SignalStatus,
    TickerSignal,
)
from hlsignals.domain.symbols import Symbol
from tests.factories import (
    NVDA,
    D,
    make_candle_series,
    make_diagnostics,
    make_fill,
    make_market_ctx,
    make_position,
    make_scored_wallet,
    make_tape_trade,
    make_wallet_record,
)

CANDLE = make_candle_series([100.0])[0]
DAY = date(2026, 9, 24)


def scored_signal(score: float) -> TickerSignal:
    return TickerSignal(
        symbol=NVDA,
        status=SignalStatus.SCORED,
        direction=SignalDirection.LONG if score > 0 else SignalDirection.SHORT,
        score=score,
        components={},
        n_wallets=3,
        reason="",
        flags=frozenset(),
        market=make_market_ctx(),
    )


def level(px: str) -> BookLevel:
    return BookLevel(px=D(px), sz=D(1), n_orders=1)


VALID_EDGES: list[tuple[str, Callable[[], object]]] = [
    ("zero-length candle", lambda: replace(CANDLE, close_ms=CANDLE.open_ms)),
    ("candle below one", lambda: replace(CANDLE, low=0.5, open=0.5, close=0.5, high=0.5)),
    ("candle without trades", lambda: replace(CANDLE, volume=0.0, n_trades=0)),
    ("fill at epoch", lambda: make_fill(time_ms=0)),
    ("sub-unit trade, zero size", lambda: make_tape_trade(px=D("0.5"), sz=D(0))),
    ("sub-unit entry price", lambda: make_position(entry_px=D("0.5"))),
    ("sub-unit book level, empty", lambda: BookLevel(px=D("0.5"), sz=D(0), n_orders=0)),
    ("sub-unit daily bar", lambda: DailyBar("NVDA", DAY, open=0.5, close=0.5)),
    ("zero prior score", lambda: make_wallet_record(raw_score=0.0)),
    ("fractional prior score", lambda: make_wallet_record(raw_score=0.5)),
    ("score +1", lambda: scored_signal(1.0)),
    ("score -1", lambda: scored_signal(-1.0)),
    ("nothing filtered out", lambda: make_diagnostics(universe_after_filters=80)),
]


@pytest.mark.parametrize(("name", "build"), VALID_EDGES, ids=[n for n, _ in VALID_EDGES])
def test_boundary_values_are_valid(name: str, build: Callable[[], object]) -> None:
    assert build() is not None, name


INVALID: list[tuple[Callable[[], object], type[Exception], str]] = [
    (lambda: make_fill(px=D(0)), ValueError, "fill px must be positive: 0"),
    (lambda: make_fill(time_ms=-1), ValueError, "fill time must be non-negative: -1"),
    (lambda: make_tape_trade(px=D(0)), ValueError, "trade px must be positive: 0"),
    (lambda: make_tape_trade(sz=D(-1)), ValueError, "trade sz must be non-negative: -1"),
    (lambda: DailyBar("NVDA", DAY, open=0.0, close=1.0), ValueError, "invalid bar NVDA 2026-09-24"),
    (lambda: DailyBar("NVDA", DAY, open=1.0, close=0.0), ValueError, "invalid bar NVDA 2026-09-24"),
    (
        lambda: make_market_ctx(oracle_px=0.0),
        ValueError,
        "market prices must be positive",
    ),
    (lambda: make_scored_wallet(trust=1.5), ValueError, "trust must be in [0, 1]: 1.5"),
    (lambda: make_scored_wallet(n_closed_lots=-1), ValueError, "negative lot count"),
    (lambda: make_scored_wallet(track_record_days=-1.0), ValueError, "negative track record"),
    (
        lambda: L2Book(NVDA, 0, bids=(level("100"), level("100")), asks=()),
        ValueError,
        "book levels out of order",
    ),
    (
        lambda: L2Book(NVDA, 0, bids=(), asks=(level("101"), level("101"))),
        ValueError,
        "book levels out of order",
    ),
    (lambda: Symbol.parse(":GOOGL"), AdapterError, "malformed symbol ':GOOGL'"),
    (lambda: Symbol.parse("xyz:"), AdapterError, "malformed symbol 'xyz:'"),
    (
        lambda: Symbol("XYZ", "NVDA"),
        AdapterError,
        "invalid dex 'XYZ' (expected lower-case alphanumerics)",
    ),
    (lambda: Symbol("xyz", "NV DA"), AdapterError, "invalid coin 'NV DA'"),
    (
        lambda: require_address("0xABC"),
        AdapterError,
        "wallet address not canonical (0x + 40 lower-case hex): '0xABC'",
    ),
    (lambda: sign_of("Buy"), NonPerpFillError, "fill dir 'Buy' is not a perp fill"),
]


@pytest.mark.parametrize(("build", "error", "message"), INVALID, ids=[m for *_, m in INVALID])
def test_rejections_carry_their_message(
    build: Callable[[], object], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=f"^{re.escape(message)}$"):
        build()

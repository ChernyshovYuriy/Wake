"""Hits the real Hyperliquid /info API. Excluded by default; run: pytest -m live"""

from __future__ import annotations

import itertools
from datetime import timedelta

import pytest

from hlsignals.core.clock import SystemClock, SystemSleeper
from hlsignals.domain.symbols import Symbol
from hlsignals.infra.gateway import HyperliquidGateway
from hlsignals.infra.pagination import FillPaging
from hlsignals.infra.transport import (
    HttpTransport,
    RateLimitedTransport,
    RetryingTransport,
    RetryPolicy,
    Transport,
    WeightTable,
)

pytestmark = pytest.mark.live

INFO_URL = "https://api.hyperliquid.xyz/info"
NVDA = Symbol("xyz", "NVDA")
# Values from docs/api-notes.md §5, §8 (config defaults arrive in Phase 8).
WEIGHTS = WeightTable(
    default_base=20,
    base={"l2Book": 2, "clearinghouseState": 2},
    items_per_extra_unit={"userFillsByTime": 20, "candleSnapshot": 60},
)


@pytest.fixture(scope="module")
def transport() -> Transport:
    clock, sleeper = SystemClock(), SystemSleeper()
    http = HttpTransport(INFO_URL, timeout_s=30.0)
    limited = RateLimitedTransport(
        http, budget=1200, window_s=60.0, weights=WEIGHTS, clock=clock, sleeper=sleeper
    )
    return RetryingTransport(limited, RetryPolicy(5, 2.0, 2.0, 30.0), sleeper)


@pytest.fixture(scope="module")
def gateway(transport: Transport) -> HyperliquidGateway:
    return HyperliquidGateway(transport, FillPaging(page_cap=2000, history_cap=10_000))


@pytest.fixture(scope="module")
def recent_trader(transport: Transport) -> str:
    """A wallet that just traded NVDA (recentTrades is only needed here, so it stays raw)."""
    trades = transport.post({"type": "recentTrades", "coin": str(NVDA)})
    user: str = trades[0]["users"][0]
    return user


def test_live_markets(gateway: HyperliquidGateway) -> None:
    assert "xyz" in {d.name for d in gateway.perp_dexs()}
    markets = {m.symbol: m for m in gateway.meta_and_ctxs("xyz")}
    assert markets[NVDA].oi_usd > 0


def test_live_book_and_candles(gateway: HyperliquidGateway) -> None:
    book = gateway.l2_book(NVDA)
    assert book.bids[0].px < book.asks[0].px
    now = SystemClock().now()
    assert gateway.candle_snapshot(NVDA, "1h", now - timedelta(days=2), now)


def test_live_fills_and_positions(gateway: HyperliquidGateway, recent_trader: str) -> None:
    since = SystemClock().now() - timedelta(days=1)
    fills = list(itertools.islice(gateway.user_fills_by_time(recent_trader, since, None), 50))
    assert any(f.symbol == NVDA for f in fills)
    gateway.clearinghouse_state(recent_trader, "xyz")  # parses without AdapterError

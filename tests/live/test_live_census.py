"""Streams real trades into the census for a few seconds. Run: pytest -m live"""

from __future__ import annotations

from datetime import timedelta

import pytest

from hlsignals.core.clock import SystemClock, SystemSleeper
from hlsignals.domain.models import TapeTrade
from hlsignals.domain.symbols import Symbol
from hlsignals.infra.tape_feed import TapeFeed, websocket_connect
from hlsignals.wallets.census.recorder import CensusRecorder
from hlsignals.wallets.census.registry import SqliteRegistry
from hlsignals.wallets.census.tape import TapeSubject

pytestmark = pytest.mark.live

WS_URL = "wss://api.hyperliquid.xyz/ws"
SYMBOLS = (Symbol("xyz", "NVDA"), Symbol("xyz", "TSLA"), Symbol("xyz", "SP500"))
TRACKED = frozenset(SYMBOLS[:2])  # SP500 streams but is not a US stock: never recorded


def test_live_census_records_counterparties() -> None:
    registry = SqliteRegistry(":memory:")
    subject = TapeSubject()
    subject.subscribe(CensusRecorder(registry, TRACKED, dedupe_capacity=10_000))
    seen: list[TapeTrade] = []

    def on_trades(trades: list[TapeTrade]) -> None:
        seen.extend(trades)
        for trade in trades:
            subject.publish(trade)

    clock = SystemClock()
    deadline = clock.now() + timedelta(seconds=45)
    feed = TapeFeed(
        url=WS_URL,
        symbols=SYMBOLS,
        on_trades=on_trades,
        connect=websocket_connect,
        sleeper=SystemSleeper(),
        recv_timeout_s=10.0,
        reconnect_delay_s=2.0,
    )
    feed.run(lambda: len([t for t in seen if t.symbol in TRACKED]) >= 5 or clock.now() > deadline)

    tracked = [t for t in seen if t.symbol in TRACKED]
    assert tracked, "no trades on tracked symbols within 45s"
    recorded = {o.address for o in registry.observations(min_fills=1)}
    assert {tracked[0].buyer, tracked[0].seller} <= recorded
    untracked_only = {t.buyer for t in seen if t.symbol not in TRACKED} - {
        a for t in tracked for a in (t.buyer, t.seller)
    }
    assert not untracked_only & recorded
    assert subject.observer_errors == 0
    registry.close()

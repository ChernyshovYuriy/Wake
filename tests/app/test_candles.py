from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from hlsignals.app.candles import CandleCache, LazyCandles
from hlsignals.core.clock import MS_PER_HOUR, from_ms, to_ms
from hlsignals.domain.models import Candle
from hlsignals.domain.symbols import Symbol
from tests.factories import AAPL, NVDA, T0_MS, make_candle_series

END = from_ms(T0_MS + 48 * MS_PER_HOUR)


@dataclass
class FakeCandles:
    calls: list[tuple[Symbol, str, datetime, datetime]] = field(default_factory=list)

    def candle_snapshot(
        self, symbol: Symbol, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        self.calls.append((symbol, interval, start, end))
        return [
            c
            for c in make_candle_series([100.0] * 48, symbol=symbol, step_ms=MS_PER_HOUR)
            if to_ms(start) <= c.open_ms and c.close_ms <= to_ms(end)
        ]


def test_same_window_fetched_once_per_symbol() -> None:
    fake = FakeCandles()
    cache = CandleCache(fake, "1h")
    first = cache.window(NVDA, END - timedelta(hours=10), END)
    assert cache.window(NVDA, END - timedelta(hours=10), END) == first
    cache.window(AAPL, END - timedelta(hours=10), END)
    assert [c[0] for c in fake.calls] == [NVDA, AAPL]
    assert fake.calls[0][1] == "1h"


def test_narrower_window_served_from_wider_one() -> None:
    fake = FakeCandles()
    cache = CandleCache(fake, "1h")
    wide = cache.window(NVDA, END - timedelta(hours=40), END)
    narrow = cache.window(NVDA, END - timedelta(hours=5), END)
    assert len(fake.calls) == 1
    assert len(narrow) == 5
    assert narrow == wide[-5:]


def test_wider_window_refetches() -> None:
    fake = FakeCandles()
    cache = CandleCache(fake, "1h")
    cache.window(NVDA, END - timedelta(hours=5), END)
    cache.window(NVDA, END - timedelta(hours=40), END)
    assert len(fake.calls) == 2


def test_lazy_candles_fetch_only_what_is_read() -> None:
    fake = FakeCandles()
    lazy = LazyCandles(
        CandleCache(fake, "1h"), frozenset({NVDA, AAPL}), END - timedelta(hours=10), END
    )
    assert fake.calls == []  # building it fetches nothing
    assert set(lazy) == {NVDA, AAPL}
    assert len(lazy) == 2
    assert len(lazy[NVDA]) == 10
    assert lazy.get(AAPL) is not None
    assert [c[0] for c in fake.calls] == [NVDA, AAPL]
    with pytest.raises(KeyError):
        lazy[Symbol("xyz", "TSLA")]
    assert lazy.get(Symbol("xyz", "TSLA")) is None

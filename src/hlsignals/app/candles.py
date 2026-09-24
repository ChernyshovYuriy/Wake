"""Per-run candle cache: each symbol's widest requested window is fetched once and any
narrower window inside it is served from memory (vetting and signals share candles)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Protocol

from hlsignals.core.clock import to_ms
from hlsignals.domain.models import Candle
from hlsignals.domain.symbols import Symbol


class CandlePort(Protocol):
    def candle_snapshot(
        self, symbol: Symbol, interval: str, start: datetime, end: datetime
    ) -> list[Candle]: ...


class CandleCache:
    def __init__(self, port: CandlePort, interval: str) -> None:
        self._port = port
        self._interval = interval
        self._windows: dict[Symbol, tuple[datetime, datetime, tuple[Candle, ...]]] = {}

    def window(self, symbol: Symbol, start: datetime, end: datetime) -> tuple[Candle, ...]:
        cached = self._windows.get(symbol)
        if cached is not None and cached[0] <= start and end <= cached[1]:
            lo, hi = to_ms(start), to_ms(end)
            return tuple(c for c in cached[2] if lo <= c.open_ms and c.close_ms <= hi)
        candles = tuple(self._port.candle_snapshot(symbol, self._interval, start, end))
        self._windows[symbol] = (start, end, candles)
        return candles


class LazyCandles(Mapping[Symbol, tuple[Candle, ...]]):
    """Candles for ``symbols`` over one window, fetched on first access per symbol.

    Filters and features only read candles when they need them (the reversal-bait
    filter, for short trips), so wallets rejected earlier cost no candle requests.
    """

    def __init__(
        self, cache: CandleCache, symbols: frozenset[Symbol], start: datetime, end: datetime
    ) -> None:
        self._cache = cache
        self._symbols = symbols
        self._start = start
        self._end = end

    def __getitem__(self, symbol: Symbol) -> tuple[Candle, ...]:
        if symbol not in self._symbols:
            raise KeyError(symbol)
        return self._cache.window(symbol, self._start, self._end)

    def __iter__(self) -> Iterator[Symbol]:
        return iter(sorted(self._symbols))

    def __len__(self) -> int:
        return len(self._symbols)

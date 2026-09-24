"""Real-stock prices for measuring backtest outcomes (never used to build signals)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Protocol

from hlsignals.domain.models import DailyBar
from hlsignals.domain.symbols import Symbol


class PriceHistory(Protocol):
    def daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]: ...


def ticker_for(symbol: Symbol, overrides: Mapping[str, str]) -> str:
    """The cash-market ticker of a HIP-3 equity perp (its coin unless overridden)."""
    return overrides.get(symbol.coin, symbol.coin)


class PriceBook:
    def __init__(self, bars: Mapping[str, Iterable[DailyBar]]) -> None:
        self._bars = {t: {b.day: b for b in rows} for t, rows in bars.items()}

    def open_on(self, ticker: str, day: date) -> float | None:
        bar = self._bars.get(ticker, {}).get(day)
        return None if bar is None else bar.open

    def close_on(self, ticker: str, day: date) -> float | None:
        bar = self._bars.get(ticker, {}).get(day)
        return None if bar is None else bar.close

    def last_day(self, ticker: str) -> date | None:
        days = self._bars.get(ticker)
        return max(days) if days else None

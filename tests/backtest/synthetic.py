"""A synthetic market with a planted, persistent trend for replay tests."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from hlsignals.app.wiring import load_calendar
from hlsignals.backtest.asof import AsOfView, HistoricalData
from hlsignals.backtest.prices import PriceBook
from hlsignals.core.clock import MS_PER_HOUR, to_ms
from hlsignals.domain.candles import price_at
from hlsignals.domain.models import (
    Candle,
    DailyBar,
    SignalDirection,
    SignalStatus,
    TickerSignal,
)
from hlsignals.domain.symbols import Symbol
from hlsignals.session.calendar import UsEquityCalendar
from tests.factories import make_market_ctx

ROOT = Path(__file__).parents[2]
CALENDAR: UsEquityCalendar = load_calendar(ROOT / "config" / "us_market_calendar.toml")
COINS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
SYMBOLS = tuple(Symbol("xyz", c) for c in COINS)
SESSIONS = tuple(s.day for s in CALENDAR.sessions(date(2026, 1, 5), date(2026, 9, 18)))
REGIME_SESSIONS = 20
DRIFT = 0.004
NOISE = 0.01


def market(seed: int = 7) -> tuple[HistoricalData, PriceBook]:
    """Daily bars per ticker with regimes lasting 20 sessions; one perp candle per
    session closing at the cash close, so a source can read the path through a view."""
    rng = random.Random(seed)
    bars: dict[str, list[DailyBar]] = {}
    candles: dict[Symbol, tuple[Candle, ...]] = {}
    for symbol in SYMBOLS:
        price, rows, perp = 100.0, [], []
        regime = 1.0
        for i, day in enumerate(SESSIONS):
            if i % REGIME_SESSIONS == 0:
                regime = rng.choice((-1.0, 1.0))
            open_px = price
            price = price * (1 + regime * DRIFT + rng.gauss(0, NOISE))
            rows.append(DailyBar(symbol.coin, day, open_px, price))
            session = CALENDAR.sessions(day, day)[0]
            close_ms = to_ms(session.close)
            perp.append(
                Candle(
                    symbol,
                    "1h",
                    close_ms - MS_PER_HOUR,
                    close_ms - 1,
                    open_px,
                    max(open_px, price),
                    min(open_px, price),
                    price,
                    1.0,
                    1,
                )
            )
        bars[symbol.coin] = rows
        candles[symbol] = tuple(perp)
    data = HistoricalData(
        records=(),
        fills={},
        candles=candles,
        markets={s: make_market_ctx(symbol=s) for s in SYMBOLS},
    )
    return data, PriceBook(bars)


def signal(symbol: Symbol, direction: SignalDirection, score: float) -> TickerSignal:
    return TickerSignal(
        symbol=symbol,
        status=SignalStatus.SCORED,
        direction=direction,
        score=score,
        components={},
        n_wallets=3,
        reason="synthetic",
        flags=frozenset(),
        market=make_market_ctx(symbol=symbol),
    )


class MomentumSource:
    """Long if the perp rose over the last 5 sessions, short if it fell (reads only the view)."""

    def signals_at(self, view: AsOfView) -> Sequence[TickerSignal]:
        out = []
        for symbol in view.markets:
            candles = view.candles(symbol)
            if len(candles) < 6:
                continue
            now = price_at(candles, view.t_ms)
            then = candles[-6].close
            if now is None or now == then:
                continue
            up = now > then
            out.append(
                signal(
                    symbol,
                    SignalDirection.LONG if up else SignalDirection.SHORT,
                    0.5 if up else -0.5,
                )
            )
        return out


class ShuffledSource:
    """The momentum signals with directions reassigned at random."""

    def __init__(self, seed: int) -> None:
        self._inner = MomentumSource()
        self._rng = random.Random(seed)

    def signals_at(self, view: AsOfView) -> Sequence[TickerSignal]:
        out = []
        for s in self._inner.signals_at(view):
            direction = self._rng.choice((SignalDirection.LONG, SignalDirection.SHORT))
            out.append(
                signal(s.symbol, direction, 0.5 if direction is SignalDirection.LONG else -0.5)
            )
        return out


class CheatingSource:
    def signals_at(self, view: AsOfView) -> Sequence[TickerSignal]:
        view.candles_between(SYMBOLS[0], view.t_ms, view.t_ms + 5 * 24 * MS_PER_HOUR)
        return []


class FixedSource:
    def __init__(self, signals: Sequence[TickerSignal]) -> None:
        self._signals = signals

    def signals_at(self, view: AsOfView) -> Sequence[TickerSignal]:
        return self._signals


def replay_days(n: int, offset: int = 10) -> list[date]:
    return list(SESSIONS[offset : offset + n])


def day_after(day: date, sessions: int) -> date:
    return SESSIONS[SESSIONS.index(day) + sessions]

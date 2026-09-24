"""Replay: for each session day, build signals as of just before the open (through an
AsOfView only), turn every long/short signal into a trade in the real stock, and measure
it forward: enter at that day's open, exit at the close of the ``horizon_sessions``-th
session. Each trade has a buy-and-hold benchmark over the same window at the same cost.

Trades whose bars are missing or whose exit has not happened yet are skipped and counted.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from hlsignals.backtest.asof import AsOfView, HistoricalData
from hlsignals.backtest.costs import CostModel
from hlsignals.backtest.metrics import Metrics, compute_metrics
from hlsignals.backtest.prices import PriceBook, ticker_for
from hlsignals.core.clock import to_ms
from hlsignals.domain.models import SignalDirection, SignalStatus, TickerSignal
from hlsignals.domain.symbols import Symbol
from hlsignals.session.calendar import SessionCalendar

_TRADED = frozenset({SignalDirection.LONG, SignalDirection.SHORT})
# Calendar days to look ahead for the exit session (covers holidays around a horizon).
_EXIT_SEARCH_FACTOR = 2
_EXIT_SEARCH_SLACK_DAYS = 10


logger = logging.getLogger(__name__)
_PROGRESS_EVERY = 10


class SignalSource(Protocol):
    def signals_at(self, view: AsOfView) -> Sequence[TickerSignal]: ...


@dataclass(frozen=True, slots=True)
class Trade:
    day: date  # entry session (enter at its open)
    exit_day: date  # exit at this session's close
    symbol: Symbol
    ticker: str
    direction: SignalDirection
    score: float
    entry_px: float
    exit_px: float
    gross: float  # direction x (exit / entry - 1)
    net: float  # gross - round-trip cost
    benchmark_net: float  # long the same stock, same window, same cost


@dataclass(frozen=True, slots=True)
class ReplayResult:
    sessions: tuple[date, ...]
    trades: tuple[Trade, ...]
    skipped: Mapping[str, int]  # reason -> count
    horizon_sessions: int

    @property
    def exposed_sessions(self) -> int:
        return sum(1 for d in self.sessions if any(t.day <= d <= t.exit_day for t in self.trades))

    def metrics(self) -> Metrics:
        return self._metrics([t.net for t in self.trades])

    def benchmark_metrics(self) -> Metrics:
        return self._metrics([t.benchmark_net for t in self.trades])

    def _metrics(self, returns: list[float]) -> Metrics:
        return compute_metrics(
            returns,
            horizon_days=self.horizon_sessions,
            sessions=max(len(self.sessions), 1),
            exposed_sessions=self.exposed_sessions,
        )


class Replay:
    def __init__(
        self,
        *,
        data: HistoricalData,
        source: SignalSource,
        prices: PriceBook,
        calendar: SessionCalendar,
        costs: CostModel,
        horizon_sessions: int,
        preopen_minutes: float,
        ticker_overrides: Mapping[str, str],
    ) -> None:
        if horizon_sessions < 1:
            raise ValueError(f"horizon_sessions must be >= 1: {horizon_sessions}")
        self._data = data
        self._source = source
        self._prices = prices
        self._calendar = calendar
        self._costs = costs
        self._horizon = horizon_sessions
        self._preopen = timedelta(minutes=preopen_minutes)
        self._overrides = ticker_overrides

    def run(self, days: Sequence[date]) -> ReplayResult:
        if not days:
            return ReplayResult((), (), {}, self._horizon)
        span = timedelta(days=self._horizon * _EXIT_SEARCH_FACTOR + _EXIT_SEARCH_SLACK_DAYS)
        sessions = self._calendar.sessions(days[0], days[-1] + span)
        index = {s.day: i for i, s in enumerate(sessions)}
        trades: list[Trade] = []
        skipped: Counter[str] = Counter()
        for n, day in enumerate(days, start=1):
            if n % _PROGRESS_EVERY == 0:
                logger.info("replayed %d/%d sessions, %d trades", n, len(days), len(trades))
            i = index.get(day)
            if i is None:
                skipped["not a session"] += 1
                continue
            view = self._data.at(to_ms(sessions[i].open - self._preopen))
            for signal in self._source.signals_at(view):
                direction = signal.direction
                if signal.status is not SignalStatus.SCORED or direction not in _TRADED:
                    continue
                # The search span always holds ``horizon`` sessions; past the calendar's
                # coverage the calendar itself raises ConfigError instead.
                exit_day = sessions[i + self._horizon - 1].day
                trade = self._trade(signal, direction, day, exit_day, skipped)
                if trade is not None:
                    trades.append(trade)
        return ReplayResult(tuple(days), tuple(trades), dict(skipped), self._horizon)

    def _trade(
        self,
        signal: TickerSignal,
        direction: SignalDirection,
        day: date,
        exit_day: date,
        skipped: Counter[str],
    ) -> Trade | None:
        ticker = ticker_for(signal.symbol, self._overrides)
        last = self._prices.last_day(ticker)
        if last is not None and exit_day > last:
            skipped["exit not yet happened"] += 1
            return None
        entry = self._prices.open_on(ticker, day)
        exit_px = self._prices.close_on(ticker, exit_day)
        if entry is None or exit_px is None:
            skipped["missing stock bars"] += 1
            return None
        sign = 1.0 if direction is SignalDirection.LONG else -1.0
        move = exit_px / entry - 1
        cost = self._costs.round_trip(ticker)
        return Trade(
            day=day,
            exit_day=exit_day,
            symbol=signal.symbol,
            ticker=ticker,
            direction=direction,
            score=signal.score or 0.0,
            entry_px=entry,
            exit_px=exit_px,
            gross=sign * move,
            net=sign * move - cost,
            benchmark_net=move - cost,
        )

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import replace
from datetime import date

import pytest

from hlsignals.backtest.costs import BpsCostModel
from hlsignals.backtest.prices import PriceBook
from hlsignals.backtest.replay import Replay, ReplayResult, SignalSource
from hlsignals.core.clock import to_ms
from hlsignals.core.errors import ConfigError, LookAheadError
from hlsignals.domain.models import DailyBar, SignalDirection, SignalStatus, TickerSignal
from tests.backtest.synthetic import (
    CALENDAR,
    SESSIONS,
    SYMBOLS,
    CheatingSource,
    FixedSource,
    MomentumSource,
    ShuffledSource,
    day_after,
    market,
    replay_days,
    signal,
)

HORIZON = 5


def replay(source: SignalSource, prices: PriceBook | None = None, cost_bps: float = 0.0) -> Replay:
    data, book = market()
    return Replay(
        data=data,
        source=source,
        prices=prices or book,
        calendar=CALENDAR,
        costs=BpsCostModel(cost_bps),
        horizon_sessions=HORIZON,
        preopen_minutes=30.0,
        ticker_overrides={},
    )


def mean_and_se(result: ReplayResult) -> tuple[float, float]:
    rs = [t.net for t in result.trades]
    return statistics.fmean(rs), statistics.stdev(rs) / math.sqrt(len(rs))


def test_planted_edge_is_recovered() -> None:
    result = replay(MomentumSource()).run(replay_days(140))
    mean, se = mean_and_se(result)
    assert len(result.trades) > 500
    assert mean > 3 * se > 0


def test_shuffled_signals_have_no_edge() -> None:
    result = replay(ShuffledSource(seed=11)).run(replay_days(140))
    mean, se = mean_and_se(result)
    assert abs(mean) < 3 * se


def test_look_ahead_attempt_fails_loudly() -> None:
    with pytest.raises(LookAheadError):
        replay(CheatingSource()).run(replay_days(3))


DAY_30 = SESSIONS[30]


def test_momentum_needs_six_sessions_of_history() -> None:
    data, _ = market()
    early = data.at(to_ms(CALENDAR.sessions(SESSIONS[3], SESSIONS[3])[0].open))
    assert MomentumSource().signals_at(early) == []


def test_momentum_is_silent_when_the_price_did_not_move() -> None:
    data, _ = market()
    flat = {
        s: tuple(replace(c, open=100.0, high=100.0, low=100.0, close=100.0) for c in cs)
        for s, cs in data.candles.items()
    }
    view = replace(data, candles=flat).at(to_ms(CALENDAR.sessions(DAY_30, DAY_30)[0].open))
    assert MomentumSource().signals_at(view) == []


DAY = SESSIONS[20]
EXIT = day_after(DAY, HORIZON - 1)
AAA = SYMBOLS[0]


def book(missing: str | None = None) -> PriceBook:
    """AAA: open 100 on DAY, close 110 on EXIT; ``missing`` ("entry" / "exit") drops that bar."""
    rows = {DAY: (100.0, 101.0), EXIT: (108.0, 110.0), SESSIONS[-1]: (120.0, 120.0)}
    if missing is not None:
        rows.pop(DAY if missing == "entry" else EXIT)
    return PriceBook({"AAA": [DailyBar("AAA", d, o, c) for d, (o, c) in rows.items()]})


def one_trade(
    direction: SignalDirection, cost_bps: float = 0.0, prices: PriceBook | None = None
) -> ReplayResult:
    source = FixedSource(
        [signal(AAA, direction, 0.4 if direction is SignalDirection.LONG else -0.4)]
    )
    return replay(source, prices or book(), cost_bps).run([DAY])


def test_long_and_short_returns_and_benchmark() -> None:
    (long_trade,) = one_trade(SignalDirection.LONG).trades
    (short_trade,) = one_trade(SignalDirection.SHORT).trades
    assert (long_trade.entry_px, long_trade.exit_px, long_trade.exit_day) == (100.0, 110.0, EXIT)
    assert long_trade.gross == pytest.approx(0.10)
    assert short_trade.gross == pytest.approx(-0.10)
    assert short_trade.benchmark_net == long_trade.benchmark_net == pytest.approx(0.10)


def test_costs_reduce_returns_exactly_by_modelled_bps() -> None:
    (trade,) = one_trade(SignalDirection.LONG, cost_bps=5.0).trades
    assert trade.net == pytest.approx(trade.gross - 0.0010)
    assert trade.benchmark_net == pytest.approx(0.10 - 0.0010)


@pytest.mark.parametrize("missing", ["entry", "exit"])
def test_missing_stock_bars_are_skipped_and_counted(missing: str) -> None:
    result = one_trade(SignalDirection.LONG, prices=book(missing))
    assert result.trades == ()
    assert result.skipped == {"missing stock bars": 1}


def test_exit_after_last_available_bar_is_not_yet_a_trade() -> None:
    prices = PriceBook({"AAA": [DailyBar("AAA", DAY, 100.0, 101.0)]})
    result = one_trade(SignalDirection.LONG, prices=prices)
    assert result.skipped == {"exit not yet happened": 1}


def test_flat_and_insufficient_signals_are_not_traded() -> None:
    flat = signal(AAA, SignalDirection.FLAT, 0.01)
    insufficient = TickerSignal(
        symbol=AAA,
        status=SignalStatus.INSUFFICIENT,
        direction=None,
        score=None,
        components={},
        n_wallets=1,
        reason="",
        flags=frozenset(),
        market=flat.market,
    )
    assert replay(FixedSource([flat, insufficient]), book()).run([DAY]).trades == ()


def test_non_session_days_and_empty_input() -> None:
    saturday = date(2026, 9, 19)
    assert replay(FixedSource([]), book()).run([saturday]).skipped == {"not a session": 1}
    empty = replay(FixedSource([]), book()).run([])
    assert empty.trades == ()
    assert empty.metrics().n_trades == 0


def test_exit_beyond_calendar_coverage_fails_loudly() -> None:
    late = CALENDAR.sessions(date(2028, 12, 26), date(2028, 12, 29))[-1].day
    source = FixedSource([signal(AAA, SignalDirection.LONG, 0.4)])
    with pytest.raises(ConfigError, match="extend"):
        replay(source, book()).run([late])


def test_exposure_counts_sessions_with_an_open_trade() -> None:
    days = list(SESSIONS[20:30])
    result = replay(FixedSource([signal(AAA, SignalDirection.LONG, 0.4)]), book()).run(days)
    assert len(result.trades) == 1  # only DAY has both bars
    assert result.exposed_sessions == HORIZON
    assert result.metrics().exposure == HORIZON / len(days)


def test_horizon_validation() -> None:
    data, prices = market()
    with pytest.raises(ValueError, match="horizon"):
        Replay(
            data=data,
            source=FixedSource([]),
            prices=prices,
            calendar=CALENDAR,
            costs=BpsCostModel(0.0),
            horizon_sessions=0,
            preopen_minutes=30.0,
            ticker_overrides={},
        )


def test_benchmark_metrics_use_benchmark_returns() -> None:
    result = one_trade(SignalDirection.SHORT, cost_bps=5.0)
    assert result.metrics().mean == pytest.approx(-0.10 - 0.001)
    assert result.benchmark_metrics().mean == pytest.approx(0.10 - 0.001)


def test_replay_logs_progress(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        replay(MomentumSource()).run(replay_days(20))
    assert "replayed 10/20 sessions" in caplog.text

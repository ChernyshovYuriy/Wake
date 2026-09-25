from __future__ import annotations

from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hlsignals.backtest.asof import HistoricalData
from hlsignals.core.clock import MS_PER_HOUR, to_ms
from hlsignals.core.errors import LookAheadError
from tests.factories import (
    AAPL,
    AS_OF,
    NVDA,
    T0_MS,
    WALLET,
    make_candle_series,
    make_market_ctx,
    make_trips,
    make_wallet_record,
)

FILLS = make_trips([0.01] * 10, hold_ms=6 * MS_PER_HOUR, gap_ms=6 * MS_PER_HOUR)
CANDLES = make_candle_series([100.0 + i for i in range(200)], step_ms=MS_PER_HOUR)
DATA = HistoricalData(
    records=(make_wallet_record(raw_score=70.0, as_of=AS_OF),),
    fills={WALLET: tuple(FILLS)},
    candles={NVDA: tuple(CANDLES)},
    markets={NVDA: make_market_ctx()},
)
END = T0_MS + 200 * MS_PER_HOUR


@given(st.integers(T0_MS - MS_PER_HOUR, END + MS_PER_HOUR))
def test_view_never_returns_rows_after_t(t: int) -> None:
    view = DATA.at(t)
    assert all(f.time_ms <= t for f in view.fills(WALLET))
    assert all(c.close_ms <= t for c in view.candles(NVDA))
    assert len(view.fills(WALLET)) == sum(1 for f in FILLS if f.time_ms <= t)
    assert len(view.candles(NVDA)) == sum(1 for c in CANDLES if c.close_ms <= t)


@given(st.integers(T0_MS, END), st.integers(1, 10 * MS_PER_HOUR))
def test_reading_beyond_t_raises(t: int, beyond: int) -> None:
    view = DATA.at(t)
    with pytest.raises(LookAheadError):
        view.candles_between(NVDA, t - MS_PER_HOUR, t + beyond)
    with pytest.raises(LookAheadError):
        view.fills_between(WALLET, t - MS_PER_HOUR, t + beyond)


# Every event time, one millisecond either side and exactly on it: the boundary that random t
# never reaches (AUDIT.md F3-2). Checked exhaustively, not sampled.
FILL_TIMES = sorted({f.time_ms for f in FILLS})
CLOSE_TIMES = sorted({c.close_ms for c in CANDLES})
OFFSETS = (-1, 0, 1)


def test_fills_are_visible_exactly_from_their_time() -> None:
    for time_ms in FILL_TIMES:
        for offset in OFFSETS:
            t = time_ms + offset
            visible = DATA.at(t).fills(WALLET)
            assert len(visible) == sum(1 for f in FILLS if f.time_ms <= t), (time_ms, offset)
            assert all(f.time_ms <= t for f in visible)


def test_candles_are_visible_exactly_from_their_close() -> None:
    for close_ms in CLOSE_TIMES:
        for offset in OFFSETS:
            t = close_ms + offset
            visible = DATA.at(t).candles(NVDA)
            assert len(visible) == sum(1 for c in CANDLES if c.close_ms <= t), (close_ms, offset)
            assert all(c.close_ms <= t for c in visible)


def test_windows_include_an_event_exactly_at_their_end() -> None:
    fill = FILLS[3]
    view = DATA.at(fill.time_ms)
    assert fill in view.fills_between(WALLET, fill.time_ms - 1, fill.time_ms)
    assert fill not in view.fills_between(WALLET, fill.time_ms, fill.time_ms)  # start is open
    candle = CANDLES[10]
    view = DATA.at(candle.close_ms)
    assert candle in view.candles_between(NVDA, candle.open_ms, candle.close_ms)
    assert candle not in view.candles_between(NVDA, candle.open_ms + 1, candle.close_ms)


def test_prior_score_taken_exactly_at_t_is_visible() -> None:
    record = DATA.records[0]
    at = to_ms(AS_OF)
    assert DATA.at(at).score(record) == 70.0
    assert DATA.at(at - 1).score(record) is None


def test_windows_within_t() -> None:
    t = T0_MS + 50 * MS_PER_HOUR
    view = DATA.at(t)
    window = view.candles_between(NVDA, t - 10 * MS_PER_HOUR, t)
    assert len(window) == 10
    assert all(t - 10 * MS_PER_HOUR <= c.open_ms and c.close_ms <= t for c in window)
    assert all(
        f.time_ms > t - 20 * MS_PER_HOUR
        for f in view.fills_between(WALLET, t - 20 * MS_PER_HOUR, t)
    )


def test_unknown_keys_are_empty() -> None:
    view = DATA.at(END)
    assert view.fills("0x" + "9" * 40) == ()
    assert view.candles(AAPL) == ()


def test_prior_score_dated_after_t_is_hidden() -> None:
    record = DATA.records[0]
    before = DATA.at(to_ms(AS_OF - timedelta(days=1)))
    after = DATA.at(to_ms(AS_OF + timedelta(days=1)))
    assert before.score(record) is None
    assert after.score(record) == 70.0


def test_fills_must_be_sorted() -> None:
    with pytest.raises(ValueError, match="sorted"):
        HistoricalData(records=(), fills={WALLET: tuple(reversed(FILLS))}, candles={}, markets={})

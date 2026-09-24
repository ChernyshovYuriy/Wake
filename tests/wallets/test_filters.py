from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from hlsignals.core.clock import MS_PER_DAY, MS_PER_HOUR
from hlsignals.domain.models import Candle, Fill
from hlsignals.domain.symbols import Symbol
from hlsignals.wallets.filters import (
    InactivityFilter,
    MakerProfileFilter,
    MinEquitySampleFilter,
    ReversalBaitFilter,
    prescreen_fill_rate,
    wallet_filter_chain,
)
from tests.factories import NVDA, T0_MS, make_candle_series, make_equity_slice, make_trips

SWING = make_equity_slice(make_trips([0.02, -0.01, 0.03, 0.01, -0.02], hold_ms=3 * MS_PER_DAY))


# --- min sample ---------------------------------------------------------------------


@pytest.mark.parametrize(("minimum", "accepted"), [(4, True), (5, True), (6, False)])
def test_min_sample_threshold_inclusive(minimum: int, accepted: bool) -> None:
    verdict = MinEquitySampleFilter(minimum).apply(SWING)
    assert verdict.accepted is accepted
    if not accepted:
        assert "5 scored round trips < 6" in verdict.reason


# --- maker / HFT profile ----------------------------------------------------------------

MAKER = MakerProfileFilter(min_taker_ratio=0.2, max_fills_per_day=50.0, min_median_hold_days=0.1)


def test_directional_swing_wallet_kept() -> None:
    assert MAKER.apply(SWING).accepted


def test_symmetric_high_frequency_maker_rejected() -> None:
    # 100 round trips per day, 30 s holds, all passive (maker) fills, tiny symmetric P&L.
    fills = make_trips([0.0005, -0.0005] * 50, hold_ms=30_000, gap_ms=60_000, crossed=False)
    verdict = MAKER.apply(make_equity_slice(fills))
    assert not verdict.accepted
    assert "taker ratio" in verdict.reason
    assert "fills/day" in verdict.reason
    assert "median hold" in verdict.reason


@pytest.mark.parametrize(
    ("maker", "accepted"),
    [
        (MakerProfileFilter(1.0, 1e9, 0.0), True),  # SWING is all taker: exactly at the floor
        (MakerProfileFilter(0.0, 1.0, 0.0), True),  # one fill per active day: at the cap
        (MakerProfileFilter(0.0, 1e9, 3.0), True),  # median hold exactly 3 days
        (MakerProfileFilter(0.0, 1e9, 3.01), False),
    ],
)
def test_maker_thresholds_inclusive(maker: MakerProfileFilter, accepted: bool) -> None:
    assert maker.apply(SWING).accepted is accepted


def test_maker_filter_without_history_does_not_crash() -> None:
    assert MAKER.apply(make_equity_slice([])).accepted  # the sample filter handles emptiness


# --- inactivity -----------------------------------------------------------------------


@pytest.mark.parametrize(("days_idle", "accepted"), [(13.0, True), (14.0, True), (14.5, False)])
def test_inactivity_threshold_inclusive(days_idle: float, accepted: bool) -> None:
    fills = make_trips([0.01])
    last = fills[-1].time_ms
    s = make_equity_slice(fills, as_of_ms=last + int(days_idle * MS_PER_DAY))
    verdict = InactivityFilter(14.0).apply(s)
    assert verdict.accepted is accepted
    if not accepted:
        assert "14.5 days" in verdict.reason


def test_inactivity_no_equity_fills() -> None:
    verdict = InactivityFilter(14.0).apply(make_equity_slice([]))
    assert not verdict.accepted
    assert "no US-stock fills" in verdict.reason


# --- reversal bait ------------------------------------------------------------------------

WINDOW_H = 24.0
BAIT = ReversalBaitFilter(window_hours=WINDOW_H, min_events=3, max_reversal_rate=0.5, min_move=0.02)


Candles = dict[Symbol, Sequence[Candle]]


def hourly(closes: Sequence[float]) -> Candles:
    return {NVDA: make_candle_series(list(closes), step_ms=MS_PER_HOUR)}


def bait_history(reverses: bool, trips: int = 4) -> tuple[list[Fill], Candles]:
    """Long trips held 2h; price rises into each exit, then falls 5% (or keeps rising)."""
    cycle = 48  # hours per trip cycle
    fills = make_trips([0.02] * trips, hold_ms=2 * MS_PER_HOUR, gap_ms=(cycle - 2) * MS_PER_HOUR)
    closes: list[float] = []
    for _ in range(trips):
        closes += [100.0, 101.0, 102.0]  # entry .. exit
        after = 96.9 if reverses else 104.0
        closes += [after] * (cycle - 3)
    return fills, hourly(closes)


def test_entries_that_precede_reversals_are_flagged() -> None:
    fills, candles = bait_history(reverses=True)
    s = make_equity_slice(fills, candles=candles, as_of_ms=T0_MS + 4 * 48 * MS_PER_HOUR)
    verdict = BAIT.apply(s)
    assert not verdict.accepted
    assert "reversed after" in verdict.reason


def test_trips_followed_by_continuation_are_kept() -> None:
    fills, candles = bait_history(reverses=False)
    s = make_equity_slice(fills, candles=candles, as_of_ms=T0_MS + 4 * 48 * MS_PER_HOUR)
    assert BAIT.apply(s).accepted


def test_too_few_evaluable_events_is_not_enough_evidence() -> None:
    fills, candles = bait_history(reverses=True, trips=2)
    s = make_equity_slice(fills, candles=candles, as_of_ms=T0_MS + 2 * 48 * MS_PER_HOUR)
    assert BAIT.apply(s).accepted


def test_events_without_prices_are_not_evaluable() -> None:
    fills, _ = bait_history(reverses=True)
    assert BAIT.apply(make_equity_slice(fills)).accepted


def test_events_whose_window_ends_after_as_of_are_not_evaluable() -> None:
    strict = ReversalBaitFilter(
        window_hours=WINDOW_H, min_events=4, max_reversal_rate=0.5, min_move=0.02
    )
    fills, candles = bait_history(reverses=True)
    full = make_equity_slice(fills, candles=candles, as_of_ms=T0_MS + 4 * 48 * MS_PER_HOUR)
    assert not strict.apply(full).accepted  # all 4 exits evaluable
    at_last_exit = make_equity_slice(fills, candles=candles, as_of_ms=max(f.time_ms for f in fills))
    assert strict.apply(at_last_exit).accepted  # the last exit's window is in the future


def test_long_holds_are_not_bait_candidates() -> None:
    assert BAIT.apply(SWING).accepted


@pytest.mark.parametrize(
    "build",
    [
        lambda: ReversalBaitFilter(0.0, 3, 0.5, 0.02),
        lambda: ReversalBaitFilter(24.0, 0, 0.5, 0.02),
        lambda: ReversalBaitFilter(24.0, 3, 1.5, 0.02),
        lambda: ReversalBaitFilter(24.0, 3, 0.5, -0.1),
    ],
)
def test_reversal_bait_validation(build: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match="reversal"):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: MinEquitySampleFilter(-1),
        lambda: MakerProfileFilter(-0.1, 1.0, 0.0),
        lambda: MakerProfileFilter(0.1, 0.0, 0.0),
        lambda: InactivityFilter(-1.0),
    ],
)
def test_threshold_validation(build: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match="threshold"):
        build()


# --- chain ----------------------------------------------------------------------------------


def test_chain_short_circuits_and_aggregates_reasons() -> None:
    chain = wallet_filter_chain([MinEquitySampleFilter(3), InactivityFilter(14.0)])
    tiny = make_equity_slice(make_trips([0.01]))
    result = chain.run([SWING, tiny, make_equity_slice([])])
    assert result.accepted == (SWING,)
    assert result.counts_by_filter() == {"min_sample": 2}


# --- fill-rate pre-screen (applied to the first page of history) ----------------------------


def test_prescreen_rejects_hyperactive_sample() -> None:
    fills = make_trips([0.0005] * 10, hold_ms=30_000, gap_ms=30_000)  # 20 fills in minutes
    verdict = prescreen_fill_rate(fills, max_fills_per_day=5.0)
    assert not verdict.accepted
    assert (
        "prescreen: 20 US-stock fills in the first page averaged 20 fills/active day > 5"
        in verdict.reason
    )


def test_prescreen_threshold_inclusive_and_empty_sample() -> None:
    fills = make_trips([0.01] * 5, hold_ms=3_600_000, gap_ms=20 * 3_600_000)  # 10 fills, 5 days
    assert prescreen_fill_rate(fills, max_fills_per_day=2.0).accepted
    assert prescreen_fill_rate([], max_fills_per_day=2.0).accepted

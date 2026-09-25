from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from hlsignals.core.chain import Filter
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
from hlsignals.wallets.scoring.slice import EquitySlice
from tests.factories import (
    AAPL,
    NVDA,
    T0_MS,
    make_candle_series,
    make_equity_slice,
    make_trips,
)

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
    # 30 s holds = 0.01 h; the 0.1-day floor = 2.40 h; 200 fills in one active day.
    assert verdict.reason == (
        "taker ratio 0.00 < 0.20; 200 fills/day > 50; median hold 0.01 h < 2.40 h"
    )


def test_short_hold_reason_is_in_hours() -> None:
    hourly = make_equity_slice(make_trips([0.01] * 3, hold_ms=MS_PER_HOUR, gap_ms=MS_PER_DAY))
    assert MAKER.apply(hourly).reason == "median hold 1.00 h < 2.40 h"


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


CYCLE_H = 48  # hours per trip cycle


def bait_history(
    pattern: Sequence[bool], *, side: str = "long", move: float = 0.05, hold_h: int = 2
) -> tuple[list[Fill], Candles]:
    """One trip per 48h cycle, held ``hold_h`` hours; the price is flat at 100 up to each exit,
    then moves by ``move`` against the trip (a reversal) where ``pattern[i]``, else with it."""
    sign = 1 if side == "long" else -1
    fills = make_trips(
        [0.02] * len(pattern),
        side=side,
        hold_ms=hold_h * MS_PER_HOUR,
        gap_ms=(CYCLE_H - hold_h) * MS_PER_HOUR,
    )
    closes: list[float] = []
    for reverses in pattern:
        after = 100.0 * (1 + (-sign if reverses else sign) * move)
        closes += [100.0] * hold_h + [after] * (CYCLE_H - hold_h)
    return fills, hourly(closes)


def after_cycles(n: int) -> int:
    """An as_of by which every one of n trips' windows has ended."""
    return T0_MS + n * CYCLE_H * MS_PER_HOUR


def bait_slice(pattern: Sequence[bool], **kwargs: Any) -> EquitySlice:
    fills, candles = bait_history(pattern, **kwargs)
    return make_equity_slice(fills, candles=candles, as_of_ms=after_cycles(len(pattern)))


def test_entries_that_precede_reversals_are_flagged() -> None:
    verdict = BAIT.apply(bait_slice([True] * 4))
    assert not verdict.accepted
    assert verdict.reason == "4/4 quick trips reversed after exit (100% > 50%)"


def test_trips_followed_by_continuation_are_kept() -> None:
    assert BAIT.apply(bait_slice([False] * 4)).accepted


def test_short_trips_followed_by_a_rise_are_flagged() -> None:
    verdict = BAIT.apply(bait_slice([True] * 4, side="short"))
    assert not verdict.accepted
    assert verdict.reason.startswith("4/4 ")
    assert BAIT.apply(bait_slice([False] * 4, side="short")).accepted  # price kept falling


@pytest.mark.parametrize("side", ["long", "short"])
def test_a_move_against_the_trip_smaller_than_min_move_is_not_a_reversal(side: str) -> None:
    assert BAIT.apply(bait_slice([True] * 4, side=side, move=0.015)).accepted  # < 0.02


@pytest.mark.parametrize("side", ["long", "short"])
def test_a_move_of_exactly_min_move_is_a_reversal(side: str) -> None:
    exact = ReversalBaitFilter(
        window_hours=WINDOW_H, min_events=3, max_reversal_rate=0.5, min_move=0.25
    )
    assert not exact.apply(bait_slice([True] * 4, side=side, move=0.25)).accepted


def test_a_reversal_rate_equal_to_the_maximum_is_accepted() -> None:
    assert BAIT.apply(bait_slice([True, False] * 3)).accepted  # 3/6 = 50%
    verdict = BAIT.apply(bait_slice([True, True, False, True, False, True]))  # 4/6
    assert not verdict.accepted
    assert verdict.reason == "4/6 quick trips reversed after exit (67% > 50%)"


def test_unevaluable_trips_in_the_middle_do_not_stop_the_evaluation() -> None:
    """Between NVDA bait trips: an AAPL trip held longer than the window, and one with no
    AAPL price at its exit (candles start later). Both are skipped; later trips still count."""
    fills, candles = bait_history([True] * 4)
    held_too_long = make_trips(
        [0.02], symbol=AAPL, hold_ms=30 * MS_PER_HOUR, t0_ms=T0_MS + 50 * MS_PER_HOUR
    )
    no_ref_price = make_trips(
        [0.02], symbol=AAPL, hold_ms=2 * MS_PER_HOUR, t0_ms=T0_MS + 82 * MS_PER_HOUR
    )
    aapl = make_candle_series([100.0] * 40, symbol=AAPL, t0_ms=T0_MS + 85 * MS_PER_HOUR)
    s = make_equity_slice(
        [*fills, *held_too_long, *no_ref_price],
        candles={**candles, AAPL: aapl},
        as_of_ms=after_cycles(4),
    )
    verdict = BAIT.apply(s)
    assert not verdict.accepted
    assert verdict.reason.startswith("4/4 ")  # the two AAPL trips are not evaluable


def test_a_trip_held_exactly_the_window_is_evaluated() -> None:
    assert not BAIT.apply(bait_slice([True] * 4, hold_h=int(WINDOW_H))).accepted


def test_too_few_evaluable_events_is_not_enough_evidence() -> None:
    assert BAIT.apply(bait_slice([True] * 2)).accepted


def test_events_without_prices_are_not_evaluable() -> None:
    fills, _ = bait_history([True] * 4)
    assert BAIT.apply(make_equity_slice(fills)).accepted


def test_events_whose_window_ends_after_as_of_are_not_evaluable() -> None:
    strict = ReversalBaitFilter(
        window_hours=WINDOW_H, min_events=4, max_reversal_rate=0.5, min_move=0.02
    )
    fills, candles = bait_history([True] * 4)
    last_exit = max(f.time_ms for f in fills)
    window_ms = int(WINDOW_H * MS_PER_HOUR)
    ends_at_as_of = make_equity_slice(fills, candles=candles, as_of_ms=last_exit + window_ms)
    assert not strict.apply(ends_at_as_of).accepted  # all 4 windows ended by as_of
    ends_after = make_equity_slice(fills, candles=candles, as_of_ms=last_exit + window_ms - 1)
    assert strict.apply(ends_after).accepted  # the last window ends 1 ms after as_of


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


def test_inactivity_without_fills_says_so() -> None:
    assert InactivityFilter(14.0).apply(make_equity_slice([])).reason == "no US-stock fills"


# --- threshold validation ---------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        lambda: MinEquitySampleFilter(0),
        lambda: MakerProfileFilter(0.0, 0.5, 0.0),
        lambda: MakerProfileFilter(1.0, 0.5, 0.0),
        lambda: InactivityFilter(0.0),
        lambda: InactivityFilter(0.5),
        lambda: ReversalBaitFilter(
            window_hours=0.5, min_events=1, max_reversal_rate=0.0, min_move=0.0
        ),
        lambda: ReversalBaitFilter(
            window_hours=0.5, min_events=1, max_reversal_rate=1.0, min_move=0.0
        ),
    ],
)
def test_threshold_edges_are_valid(build: Callable[[], Filter[EquitySlice]]) -> None:
    assert build().name


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: MinEquitySampleFilter(-1), "invalid threshold: min_round_trips=-1"),
        (lambda: MakerProfileFilter(1.5, 1.0, 0.0), "invalid threshold: taker=1.5"),
        (lambda: MakerProfileFilter(-0.1, 1.0, 0.0), "invalid threshold: taker=-0.1"),
        (lambda: MakerProfileFilter(0.5, 0.0, 0.0), "invalid threshold: fills/day=0.0"),
        (lambda: MakerProfileFilter(0.5, 1.0, -1.0), "invalid threshold: min_median_hold_days"),
        (lambda: InactivityFilter(-1.0), "invalid threshold: max_days_since_last_fill"),
    ],
)
def test_invalid_thresholds_name_the_parameter(build: Callable[[], object], message: str) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        build()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"window_hours": 0.0},
        {"min_events": 0},
        {"max_reversal_rate": 1.5},
        {"max_reversal_rate": -0.1},
        {"min_move": -0.01},
    ],
)
def test_invalid_reversal_bait_parameters(kwargs: dict[str, Any]) -> None:
    valid = ReversalBaitFilter(
        window_hours=24.0, min_events=3, max_reversal_rate=0.5, min_move=0.02
    )
    with pytest.raises(ValueError, match=r"^invalid reversal-bait parameters"):
        dataclasses.replace(valid, **kwargs)


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

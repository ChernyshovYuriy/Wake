from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hlsignals.core.clock import MS_PER_DAY
from hlsignals.core.errors import NonPerpFillError
from hlsignals.domain.direction import signed_size
from hlsignals.domain.lots import LotBook
from hlsignals.domain.models import Fill, PositionSide
from tests.factories import AAPL, BTC, HOUR_MS, NVDA, T0_MS, D, make_fill, make_fills, mirror


def book_of(*fill_lists: list[Fill]) -> LotBook:
    book = LotBook()
    for fills in fill_lists:
        book.add_all(fills)
    return book


def test_open_then_full_close() -> None:
    book = book_of(make_fills([("Open Long", 2, 100), ("Close Long", 2, 110)]))
    (lot,) = book.closed_lots
    assert lot.side is PositionSide.LONG
    assert (lot.size, lot.entry_px, lot.exit_px) == (D(2), D(100), D(110))
    assert lot.realized_pnl == D(20)
    assert lot.holding_ms == HOUR_MS
    assert not lot.is_orphan
    assert book.position(NVDA) == 0


def test_short_round_trip_pnl_sign() -> None:
    book = book_of(make_fills([("Open Short", 1, 100), ("Close Short", 1, 90)]))
    (lot,) = book.closed_lots
    assert lot.side is PositionSide.SHORT
    assert lot.realized_pnl == D(10)


def test_partial_closes_are_fifo() -> None:
    book = book_of(
        make_fills(
            [
                ("Open Long", 1, 100),
                ("Open Long", 1, 120),  # scale in
                ("Close Long", "1.5", 130),  # closes lot 1 fully, lot 2 half
                ("Close Long", "0.5", 140),  # scale out
            ]
        )
    )
    lots = book.closed_lots
    assert [(lot.size, lot.entry_px, lot.exit_px) for lot in lots] == [
        (D(1), D(100), D(130)),
        (D("0.5"), D(120), D(130)),
        (D("0.5"), D(120), D(140)),
    ]
    assert sum(lot.realized_pnl or 0 for lot in lots) == D(30 + 5 + 10)


def test_flip_through_zero() -> None:
    book = book_of(
        make_fills([("Open Long", 2, 100), ("Long > Short", 5, 90), ("Close Short", 3, 80)])
    )
    first, second = book.closed_lots
    assert (first.side, first.size, first.realized_pnl) == (PositionSide.LONG, D(2), D(-20))
    assert (second.side, second.size, second.entry_px) == (PositionSide.SHORT, D(3), D(90))
    assert second.realized_pnl == D(30)
    assert book.position(NVDA) == 0


def test_orphan_close_from_truncated_history_is_flagged_not_guessed() -> None:
    book = book_of(make_fills([("Close Long", 3, 110)], start_position=5))
    (lot,) = book.closed_lots
    assert lot.is_orphan
    assert lot.entry_px is None
    assert lot.realized_pnl is None
    assert lot.holding_ms is None
    assert lot.holding_days is None
    assert book.position(NVDA) == D(2)
    assert book.gaps == ()  # truncation at the start is not a mid-history gap


def test_mid_history_gap_is_recorded_and_resynced() -> None:
    first = make_fills([("Open Long", 1, 100)])
    later = make_fills([("Close Long", 4, 120)], start_position=4, t0_ms=T0_MS + HOUR_MS)
    book = book_of(first, later)
    (gap,) = book.gaps
    assert (gap.symbol, gap.tracked, gap.reported) == (NVDA, D(1), D(4))
    assert (gap.time_ms, gap.tid) == (later[0].time_ms, later[0].tid)  # the offending fill
    (lot,) = book.closed_lots
    assert lot.is_orphan  # the known lot was discarded: tracked state proved wrong
    assert book.position(NVDA) == 0


def test_gap_to_flat_leaves_no_lots() -> None:
    first = make_fills([("Open Long", 1, 100)])
    later = make_fills([("Open Short", 1, 100)], start_position=0, t0_ms=T0_MS + HOUR_MS)
    book = book_of(first, later)
    assert len(book.gaps) == 1
    assert book.position(NVDA) == D(-1)


def test_interleaved_coins_are_isolated() -> None:
    nvda = make_fills([("Open Long", 1, 100), ("Close Long", 1, 110)], symbol=NVDA)
    aapl = make_fills([("Open Short", 2, 50)], symbol=AAPL, t0_ms=T0_MS + 1)
    book = LotBook()
    for fill in sorted(nvda + aapl, key=lambda f: f.time_ms):
        book.add(fill)
    assert book.position(NVDA) == 0
    assert book.position(AAPL) == D(-2)
    assert book.symbols == frozenset({NVDA, AAPL})
    assert [lot.symbol for lot in book.closed_lots] == [NVDA]


def test_same_timestamp_keeps_feed_order_not_tid_order() -> None:
    fills = make_fills([("Open Long", 1, 100), ("Close Long", 1, 105)], step_ms=0)
    # tids deliberately descending: order must still follow the feed (API) order.
    reordered = [replace(f, tid=100 - i) for i, f in enumerate(fills)]
    book = book_of(reordered)
    assert book.closed_lots[0].realized_pnl == D(5)
    assert book.closed_lots[0].holding_ms == 0


def test_out_of_order_fill_rejected() -> None:
    book = LotBook()
    book.add(make_fill(time_ms=T0_MS + 1))
    with pytest.raises(ValueError, match="time order"):
        book.add(make_fill(time_ms=T0_MS))


def test_non_perp_fill_rejected() -> None:
    with pytest.raises(NonPerpFillError):
        LotBook().add(make_fill(symbol=BTC, dir="Buy"))


def test_holding_time_units() -> None:
    book = book_of(
        make_fills([("Open Long", 1, 100), ("Close Long", 1, 100)], step_ms=MS_PER_DAY * 2)
    )
    (lot,) = book.closed_lots
    assert lot.holding_ms == 2 * MS_PER_DAY
    assert lot.holding_days == 2.0


def test_net_position_as_of() -> None:
    fills = make_fills([("Open Long", 2, 100), ("Close Long", 1, 100), ("Close Long", 1, 100)])
    book = book_of(fills)
    assert book.net_position(NVDA, T0_MS - 1) == 0  # before first fill, started flat
    assert book.net_position(NVDA, T0_MS) == D(2)  # inclusive at fill time
    assert book.net_position(NVDA, T0_MS + HOUR_MS + 1) == D(1)  # between fills
    assert book.net_position(NVDA, T0_MS + 10 * HOUR_MS) == 0  # after last
    assert book.net_position(AAPL, T0_MS) == 0  # never traded


def test_net_position_before_truncated_history_is_unknown() -> None:
    book = book_of(make_fills([("Close Long", 1, 100)], start_position=3))
    assert book.net_position(NVDA, T0_MS - 1) is None
    assert book.net_position(NVDA, T0_MS) == D(2)


def test_same_ms_fills_net_position_is_after_all_of_them() -> None:
    book = book_of(make_fills([("Open Long", 1, 100), ("Open Long", 2, 100)], step_ms=0))
    assert book.net_position(NVDA, T0_MS) == D(3)


def test_zero_size_fill_changes_nothing() -> None:
    book = book_of(make_fills([("Open Long", 0, 100)]))
    assert book.closed_lots == ()
    assert book.position(NVDA) == 0


def test_realized_pnl_agrees_with_api_closed_pnl_flat_to_flat() -> None:
    # HL computes closedPnl against average entry; over a flat-to-flat round trip the total
    # equals FIFO PnL (verified on live fills, docs/api-notes.md §3).
    fills = make_fills(
        [
            ("Open Long", 1, 100),
            ("Open Long", 1, 120),
            ("Close Long", 1, 130),
            ("Close Long", 1, 140),
        ]
    )
    api = [D(0), D(0), D(20), D(30)]  # avg entry 110
    fills = [replace(f, closed_pnl=p) for f, p in zip(fills, api, strict=True)]
    book = book_of(fills)
    assert (
        sum(lot.realized_pnl or 0 for lot in book.closed_lots) == book.api_closed_pnl(NVDA) == D(50)
    )


dirs = st.sampled_from(
    ["Open Long", "Close Long", "Open Short", "Close Short", "Long > Short", "Short > Long"]
)
sizes = st.decimals(min_value="0.001", max_value=100, places=3)
prices = st.decimals(min_value=1, max_value=1000, places=2)


@given(st.lists(st.tuples(dirs, sizes, prices), max_size=30))
def test_invariants(steps: list[tuple[str, Decimal, Decimal]]) -> None:
    fills = make_fills(steps)
    book = book_of(fills)
    assert book.position(NVDA) == sum((signed_size(f) for f in fills), Decimal(0))
    assert book.gaps == ()
    assert all(lot.size > 0 and not lot.is_orphan for lot in book.closed_lots)
    mirrored = book_of([mirror(f) for f in fills])
    assert mirrored.position(NVDA) == -book.position(NVDA)
    # Same prices, opposite side: every lot's PnL is negated.
    assert [lot.realized_pnl for lot in mirrored.closed_lots] == [
        -(lot.realized_pnl or 0) for lot in book.closed_lots
    ]


# --- round trips (flat -> position -> flat) ---------------------------------------------


def test_round_trip_aggregates_scaled_entries_and_exits() -> None:
    book = book_of(
        make_fills(
            [
                ("Open Long", 1, 100),
                ("Open Long", 1, 110),
                ("Close Long", "0.5", 120),
                ("Close Long", "1.5", 130),
            ]
        )
    )
    (trip,) = book.round_trips
    assert trip.side is PositionSide.LONG
    assert (trip.open_ms, trip.close_ms) == (T0_MS, T0_MS + 3 * HOUR_MS)
    assert trip.entry_notional == D(210)
    assert trip.realized_pnl == sum((lot.realized_pnl or 0 for lot in book.closed_lots), Decimal(0))
    assert trip.realized_pnl is not None
    assert trip.return_frac == pytest.approx(float(trip.realized_pnl / D(210)))
    assert trip.holding_ms == 3 * HOUR_MS
    assert not trip.is_orphan


def test_open_position_is_not_a_round_trip_yet() -> None:
    book = book_of(make_fills([("Open Short", 1, 100), ("Close Short", "0.5", 90)]))
    assert book.round_trips == ()


def test_flip_closes_one_trip_and_opens_the_next() -> None:
    book = book_of(
        make_fills([("Open Long", 2, 100), ("Long > Short", 5, 90), ("Close Short", 3, 80)])
    )
    long_trip, short_trip = book.round_trips
    assert (long_trip.side, long_trip.realized_pnl, long_trip.entry_notional) == (
        PositionSide.LONG,
        D(-20),
        D(200),
    )
    assert (short_trip.side, short_trip.realized_pnl, short_trip.entry_notional) == (
        PositionSide.SHORT,
        D(30),
        D(270),
    )
    assert short_trip.open_ms == long_trip.close_ms


def test_trip_started_before_history_is_orphan() -> None:
    book = book_of(make_fills([("Close Long", 3, 110)], start_position=3))
    (trip,) = book.round_trips
    assert trip.side is PositionSide.LONG  # known from start_position
    assert trip.entry_notional is None
    assert trip.is_orphan
    assert trip.open_ms is None
    assert trip.realized_pnl is None
    assert trip.return_frac is None
    assert trip.holding_ms is None


def test_a_gap_to_flat_discards_the_open_trip() -> None:
    """Tracked long 1, but the next fill reports flat: the long's trip is gone, and the short
    that follows is a clean trip of its own, not a continuation of the stale long."""
    first = make_fills([("Open Long", 1, 100)])
    later = make_fills(
        [("Open Short", 1, 100), ("Close Short", 1, 90)], start_position=0, t0_ms=T0_MS + HOUR_MS
    )
    later[1] = replace(later[1], start_position=D(-1))
    (trip,) = book_of(first, later).round_trips
    assert trip.side is PositionSide.SHORT
    assert trip.open_ms == later[0].time_ms
    assert trip.realized_pnl == D(10)
    assert trip.entry_notional == D(100)


def test_gap_mid_trip_makes_it_orphan() -> None:
    first = make_fills([("Open Long", 1, 100)])
    later = make_fills([("Close Long", 2, 120)], start_position=2, t0_ms=T0_MS + HOUR_MS)
    (trip,) = book_of(first, later).round_trips
    assert trip.is_orphan


def test_round_trips_per_symbol_are_independent() -> None:
    nvda = make_fills([("Open Long", 1, 100), ("Close Long", 1, 110)], symbol=NVDA)
    aapl = make_fills([("Open Short", 1, 50)], symbol=AAPL, t0_ms=T0_MS + 1)
    book = LotBook()
    book.add_all(sorted(nvda + aapl, key=lambda f: f.time_ms))
    assert [t.symbol for t in book.round_trips] == [NVDA]


@given(st.lists(st.tuples(dirs, sizes, prices), max_size=30))
def test_round_trip_pnl_sums_to_closed_lot_pnl_when_flat(
    steps: list[tuple[str, Decimal, Decimal]],
) -> None:
    book = book_of(make_fills(steps))
    if book.position(NVDA) == 0:
        trips = sum((t.realized_pnl or Decimal(0) for t in book.round_trips), Decimal(0))
        lots = sum((lot.realized_pnl or Decimal(0) for lot in book.closed_lots), Decimal(0))
        assert trips == lots
    assert all(t.entry_notional is None or t.entry_notional > 0 for t in book.round_trips)

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from hlsignals.core.clock import MS_PER_DAY
from hlsignals.domain.models import Fill
from hlsignals.wallets.scoring.slice import EquitySlice
from tests.factories import AAPL, BTC, HOUR_MS, NVDA, T0_MS, WALLET, D, make_fills, make_position

EQUITIES = frozenset({NVDA, AAPL})
AS_OF = T0_MS + 30 * MS_PER_DAY


def slice_of(fills: list[Fill], as_of_ms: int = AS_OF, **kwargs: Any) -> EquitySlice:
    return EquitySlice.from_history(
        WALLET, fills, positions=[], equities=EQUITIES, as_of_ms=as_of_ms, **kwargs
    )


def test_crypto_only_wallet_is_empty() -> None:
    fills = make_fills([("Open Long", 1, 100), ("Close Long", 1, 110)], symbol=BTC)
    s = slice_of(fills)
    assert s.fills == ()
    assert s.round_trips == ()
    assert s.n_scored_trips == 0
    assert s.last_fill_ms is None
    assert s.days_since_last_fill is None
    assert s.track_record_days == 0.0
    assert s.fills_per_active_day == 0.0
    assert s.taker_ratio is None
    assert s.median_hold_days is None


def test_crypto_fills_and_positions_excluded_equity_kept() -> None:
    crypto = make_fills([("Open Long", 1, 100)], symbol=BTC)
    equity = make_fills([("Open Long", 1, 100), ("Close Long", 1, 110)], t0_ms=T0_MS + 1)
    s = EquitySlice.from_history(
        WALLET,
        crypto + equity,
        positions=[make_position(symbol=BTC), make_position()],
        equities=EQUITIES,
        as_of_ms=AS_OF,
    )
    assert {f.symbol for f in s.fills} == {NVDA}
    assert [p.symbol for p in s.positions] == [NVDA]
    assert s.n_scored_trips == 1


def test_fills_after_as_of_are_ignored() -> None:
    fills = make_fills([("Open Long", 1, 100), ("Close Long", 1, 110)])
    s = slice_of(fills, as_of_ms=T0_MS)
    assert len(s.fills) == 1
    assert s.round_trips == ()


def test_stats() -> None:
    fills = make_fills(
        [
            ("Open Long", 1, 100),
            ("Close Long", 1, 110),  # win, held 1 day
            ("Open Short", 1, 100),
            ("Close Short", 1, 105),
        ],  # loss, held 1 day
        step_ms=MS_PER_DAY,
    )
    fills[0] = replace(fills[0], crossed=False)
    s = slice_of(fills)
    assert s.n_scored_trips == 2
    assert s.wins == 1
    assert s.median_hold_days == 1.0
    assert s.taker_ratio == 0.75
    assert s.fills_per_active_day == 1.0
    assert s.track_record_days == 3.0
    assert s.days_since_last_fill == pytest.approx(27.0)
    assert [t.return_frac for t in s.scored_trips] == [pytest.approx(0.1), pytest.approx(-0.05)]


def test_orphan_trips_are_not_scored() -> None:
    fills = make_fills(
        [("Close Long", 1, 110), ("Open Long", 1, 100), ("Close Long", 1, 120)], start_position=1
    )
    s = slice_of(fills)
    assert len(s.round_trips) == 2
    assert s.n_scored_trips == 1


def test_candles_and_score_carried() -> None:
    s = slice_of([], candles={NVDA: ()}, raw_score=42.0, truncated=True)
    assert s.raw_score == 42.0
    assert s.truncated
    assert NVDA in s.candles


def test_history_is_not_truncated_unless_said_so() -> None:
    assert slice_of([]).truncated is False


def test_same_day_fills_count_one_active_day() -> None:
    s = slice_of(make_fills([("Open Long", 1, 100), ("Close Long", 1, 100)], step_ms=HOUR_MS))
    assert s.fills_per_active_day == 2.0


def test_net_sizes_at_as_of() -> None:
    fills = make_fills([("Open Long", 2, 100), ("Close Long", 1, 100)]) + make_fills(
        [("Open Short", 3, 50)], symbol=AAPL, t0_ms=T0_MS + 10 * HOUR_MS
    )
    s = slice_of(sorted(fills, key=lambda f: f.time_ms))
    assert s.net_sizes == {NVDA: D(1), AAPL: D(-3)}
    assert slice_of(fills[:1], as_of_ms=T0_MS - 1).net_sizes == {}

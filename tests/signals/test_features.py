from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import replace

import pytest

from hlsignals.core.clock import MS_PER_HOUR
from hlsignals.domain.models import Fill, MarketCtx, SignalComponent
from hlsignals.signals.features import FlowFeature, OvernightFeature, PositioningFeature
from tests.factories import (
    T0_MS,
    D,
    make_candle_series,
    make_fill,
    make_market_ctx,
    make_position,
    make_scored_wallet,
    make_ticker_inputs,
    wallet_address,
)

A, B, C = (wallet_address(i) for i in (1, 2, 3))
WA, WB, WC = (make_scored_wallet(address=a, trust=0.5) for a in (A, B, C))

# --- positioning ------------------------------------------------------------------------

POSITIONING = PositioningFeature()


def test_positioning_no_wallets() -> None:
    component = POSITIONING.compute(make_ticker_inputs())
    assert component.value == 0.0
    assert component.evidence["n_wallets"] == 0


@pytest.mark.parametrize(("sizes", "tilt"), [((1, 2), 1.0), ((-1, -3), -1.0), ((2, -2), 0.0)])
def test_positioning_tilt(sizes: tuple[int, int], tilt: float) -> None:
    positions = [make_position(wallet=a, size=D(s)) for a, s in zip((A, B), sizes, strict=True)]
    component = POSITIONING.compute(make_ticker_inputs(wallets=[WA, WB], positions=positions))
    assert component.value == pytest.approx(tilt)
    assert component.evidence["n_long"] == sum(1 for s in sizes if s > 0)
    assert component.evidence["n_short"] == sum(1 for s in sizes if s < 0)


def test_positioning_flat_positions_gross_zero() -> None:
    component = POSITIONING.compute(
        make_ticker_inputs(wallets=[WA], positions=[make_position(wallet=A, size=D(0))])
    )
    assert component.value == 0.0
    assert component.evidence["gross_usd"] == 0.0


def test_positioning_trust_weighting() -> None:
    trusted, doubtful = replace(WA, trust=0.9), replace(WB, trust=0.1)
    positions = [make_position(wallet=A, size=D(1)), make_position(wallet=B, size=D(-1))]
    component = POSITIONING.compute(
        make_ticker_inputs(wallets=[trusted, doubtful], positions=positions)
    )
    assert component.value == pytest.approx((0.9 - 0.1) / (0.9 + 0.1))


def test_positioning_counts_only_non_zero_sides() -> None:
    positions = [
        make_position(wallet=A, size=D("0.5")),  # a fractional long is long, not short
        make_position(wallet=B, size=D(0)),  # flat: neither
    ]
    component = POSITIONING.compute(make_ticker_inputs(wallets=[WA, WB], positions=positions))
    assert (component.evidence["n_long"], component.evidence["n_short"]) == (1, 0)
    assert component.value == 1.0


def test_positioning_uses_current_mark_price() -> None:
    market = make_market_ctx(mark_px=250.0)
    component = POSITIONING.compute(
        make_ticker_inputs(
            wallets=[WA], positions=[make_position(wallet=A, size=D(2))], market=market
        )
    )
    assert component.evidence["net_usd"] == pytest.approx(0.5 * 2 * 250.0)


# --- flow ---------------------------------------------------------------------------------

AS_OF = T0_MS + 48 * MS_PER_HOUR
FLOW = FlowFeature(window_hours=24.0, min_oi_frac=0.02, full_scale_oi_frac=0.10)
MARKET = make_market_ctx(mark_px=100.0, open_interest=10_000.0)  # OI $1,000,000


def buy(wallet: str, usd: float, t: int) -> Fill:
    return make_fill(wallet=wallet, dir="Open Long", px=D(100), sz=D(usd / 100), time_ms=t)


def flow_of(*fills: Fill, market: MarketCtx = MARKET) -> SignalComponent:
    return FLOW.compute(
        make_ticker_inputs(wallets=[WA, WB], fills=fills, market=market, as_of_ms=AS_OF)
    )


def test_flow_no_fills() -> None:
    component = flow_of()
    assert component.value == 0.0


def test_flow_window_is_open_at_start_and_closed_at_as_of() -> None:
    start = AS_OF - 24 * MS_PER_HOUR
    component = flow_of(buy(A, 60_000, start), buy(A, 60_000, start + 1), buy(B, 60_000, AS_OF))
    # trust 0.5 x ($60k + $60k) = $60k = 6% of OI; the fill exactly at window start is out.
    assert component.evidence["flow_usd"] == pytest.approx(60_000)
    assert component.evidence["n_fills"] == 2
    assert component.value == pytest.approx(0.06 / 0.10)


def test_flow_after_as_of_is_ignored() -> None:
    component = flow_of(buy(A, 500_000, AS_OF + 1))
    assert component.value == 0.0


def test_flow_saturates() -> None:
    assert flow_of(buy(A, 900_000, AS_OF)).value == 1.0


def test_flow_below_floor_zeroed_with_note() -> None:
    component = flow_of(buy(A, 30_000, AS_OF))  # 0.5 x 30k = 1.5% of OI < 2%
    assert component.value == 0.0
    assert "below" in str(component.evidence["note"])


def test_flow_zero_open_interest() -> None:
    component = flow_of(buy(A, 30_000, AS_OF), market=make_market_ctx(open_interest=0.0))
    assert component.value == 0.0
    assert component.evidence["note"] == "no open interest: flow cannot be normalized"


def test_flow_exactly_at_the_floor_is_kept() -> None:
    component = flow_of(buy(A, 40_000, AS_OF))  # 0.5 x 40k = $20k = exactly 2% of OI
    assert component.evidence["flow_oi_frac"] == 0.02
    assert component.value == pytest.approx(0.02 / 0.10)
    assert "note" not in component.evidence


def test_flow_normalizes_by_any_positive_open_interest() -> None:
    tiny = make_market_ctx(mark_px=100.0, open_interest=0.005)  # $0.50 of OI
    assert flow_of(buy(A, 100, AS_OF), market=tiny).value == 1.0


# --- overnight -------------------------------------------------------------------------------

CLOSE_MS = T0_MS + 3 * MS_PER_HOUR  # candles: [T0, T0+1h), [T0+1h, T0+2h), ...
OVERNIGHT = OvernightFeature(min_move=0.003, full_scale_move=0.03, max_staleness_hours=2.0)


def overnight(
    closes: list[float], as_of_ms: int = T0_MS + 6 * MS_PER_HOUR, t0: int = T0_MS
) -> SignalComponent:
    candles = make_candle_series(closes, t0_ms=t0, step_ms=MS_PER_HOUR)
    return OVERNIGHT.compute(
        make_ticker_inputs(candles=candles, as_of_ms=as_of_ms, last_close_ms=CLOSE_MS)
    )


def test_overnight_no_candles() -> None:
    component = overnight([])
    assert component.value == 0.0
    assert "no candle" in str(component.evidence["note"])


def test_overnight_single_candle_before_close_has_no_latest_move() -> None:
    component = overnight([100.0], as_of_ms=CLOSE_MS)
    assert component.value == 0.0


def test_overnight_all_candles_after_close() -> None:
    component = overnight([100.0, 101.0], t0=CLOSE_MS + 1)
    assert component.value == 0.0
    assert component.evidence == {"note": "no candle closed by the last close", "stale": True}


def test_overnight_move_exactly_at_the_floor_is_kept() -> None:
    floor = math.log(102.0 / 100.0)
    feature = OvernightFeature(min_move=floor, full_scale_move=0.03, max_staleness_hours=2.0)
    candles = make_candle_series([100.0, 100.0, 100.0, 102.0], step_ms=MS_PER_HOUR)
    inputs = make_ticker_inputs(
        candles=candles, as_of_ms=T0_MS + 4 * MS_PER_HOUR, last_close_ms=CLOSE_MS
    )
    component = feature.compute(inputs)
    assert component.value == pytest.approx(floor / 0.03)
    assert "note" not in component.evidence


def test_overnight_large_drop_saturates_at_minus_one() -> None:
    assert overnight([100.0, 100.0, 100.0, 90.0, 90.0, 90.0]).value == -1.0


def test_overnight_staleness_is_strictly_beyond_the_limit() -> None:
    closes = [100.0, 100.0, 100.0, 104.0]  # the latest candle closes at T0 + 4h - 1 ms
    latest_close = T0_MS + 4 * MS_PER_HOUR - 1
    at_limit = overnight(closes, as_of_ms=latest_close + 2 * MS_PER_HOUR)
    assert at_limit.evidence["stale"] is False
    beyond = overnight(closes, as_of_ms=latest_close + 2 * MS_PER_HOUR + 1)
    assert beyond.evidence["stale"] is True


def test_overnight_candle_closing_exactly_at_as_of_is_the_latest() -> None:
    strict = OvernightFeature(min_move=0.003, full_scale_move=0.03, max_staleness_hours=0.5)
    candles = make_candle_series([100.0, 100.0, 100.0, 101.0], step_ms=MS_PER_HOUR)
    as_of = candles[-1].close_ms
    inputs = make_ticker_inputs(candles=candles, as_of_ms=as_of, last_close_ms=CLOSE_MS)
    component = strict.compute(inputs)
    assert component.evidence["last_px"] == 101.0
    assert component.evidence["stale"] is False  # 0 ms old, not the previous candle's 1 h


def test_overnight_reference_is_the_candle_closing_at_the_close() -> None:
    # candle 2 covers [T0+2h, T0+3h) and closes at CLOSE_MS - 1: that is the 16:00 price.
    component = overnight([90.0, 95.0, 100.0, 101.0, 102.0, 102.0])
    assert component.evidence["ref_px"] == 100.0
    assert component.evidence["last_px"] == 102.0
    assert component.evidence["pct"] == pytest.approx(0.02)
    assert component.value == pytest.approx(math.log(1.02) / 0.03)
    assert component.evidence["stale"] is False


def test_overnight_below_floor_zeroed() -> None:
    component = overnight([100.0, 100.0, 100.0, 100.1, 100.2, 100.2])
    assert component.value == 0.0
    assert "below" in str(component.evidence["note"])


def test_overnight_stale_when_latest_candle_too_old() -> None:
    component = overnight([100.0, 100.0, 100.0, 104.0], as_of_ms=T0_MS + 10 * MS_PER_HOUR)
    assert component.evidence["stale"] is True
    assert component.value == 1.0  # +4% saturates the 3% full scale


@pytest.mark.parametrize(
    "build",
    [
        lambda: FlowFeature(0.0, 0.02, 0.1),
        lambda: FlowFeature(24.0, -0.1, 0.1),
        lambda: FlowFeature(24.0, 0.2, 0.1),
        lambda: OvernightFeature(-0.1, 0.03, 2.0),
        lambda: OvernightFeature(0.05, 0.03, 2.0),
        lambda: OvernightFeature(0.003, 0.03, 0.0),
    ],
)
def test_parameter_validation(build: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match="invalid"):
        build()


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: FlowFeature(0.0, 0.02, 0.1), "window_hours=0.0"),
        (lambda: FlowFeature(24.0, 0.1, 0.1), "need 0 <= min_oi_frac < full_scale"),
        (lambda: OvernightFeature(0.03, 0.03, 2.0), "need 0 <= min_move < full_scale_move"),
        (lambda: OvernightFeature(0.003, 0.03, 0.0), "max_staleness_hours=0.0"),
    ],
)
def test_parameter_validation_names_the_parameter(
    build: Callable[[], object], message: str
) -> None:
    full = f"invalid signal feature parameter: {message}"
    with pytest.raises(ValueError, match=f"^{re.escape(full)}$"):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: FlowFeature(0.5, 0.0, 0.1),  # sub-hour window, no floor
        lambda: OvernightFeature(0.0, 0.03, 0.5),  # no floor, 30 min staleness
    ],
)
def test_parameter_edges_are_valid(build: Callable[[], object]) -> None:
    assert build() is not None

from __future__ import annotations

import math
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
    assert "open interest" in str(component.evidence["note"])


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
    assert "last close" in str(component.evidence["note"])


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

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hlsignals.core.errors import AdapterError, NonPerpFillError
from hlsignals.domain.direction import (
    AUTO_DELEVERAGING,
    NON_PERP_DIRS,
    PERP_DIR_SIGN,
    SETTLEMENT,
    is_perp_dir,
    sign_of,
    signed_notional,
    signed_size,
)
from hlsignals.domain.models import Side
from hlsignals.infra.adapters import adapt_fills
from tests.conftest import load_fixture
from tests.factories import D, make_fill, mirror

EXPECTED_SIGN = {
    "Open Long": 1,
    "Close Short": 1,
    "Short > Long": 1,
    "Close Long": -1,
    "Open Short": -1,
    "Long > Short": -1,
}
NON_PERP = ["Buy", "Sell", "Merge Outcome", "Spot Dust Conversion"]


@pytest.mark.parametrize(("direction", "sign"), EXPECTED_SIGN.items())
def test_sign_table(direction: str, sign: int) -> None:
    assert sign_of(direction) == sign
    assert is_perp_dir(direction)


CATALOG = load_fixture("dir_catalog")["response"]


def test_every_mapped_dir_has_a_real_captured_fill() -> None:
    """Rule 10 / api-notes §4: a dir value is mapped only once a real fill shows it. The catalog
    holds one captured fill per mapped value, so a mapping cannot exist on inference alone."""
    assert set(CATALOG) == set(PERP_DIR_SIGN) | NON_PERP_DIRS | {SETTLEMENT, AUTO_DELEVERAGING}


@pytest.mark.parametrize("direction", sorted(CATALOG))
def test_catalog_fill_agrees_with_the_mapping(direction: str) -> None:
    raw = CATALOG[direction]
    assert raw["dir"] == direction
    if direction in {SETTLEMENT, AUTO_DELEVERAGING}:
        assert Decimal(raw["startPosition"]) != 0  # closes an open position (tested below)
    elif is_perp_dir(direction):
        assert (sign_of(direction) > 0) == (raw["side"] == Side.BUY)
    else:
        with pytest.raises(NonPerpFillError):
            sign_of(direction)


@pytest.mark.parametrize("direction", NON_PERP)
def test_known_non_perp_dirs_raise_distinct_error(direction: str) -> None:
    assert not is_perp_dir(direction)
    with pytest.raises(NonPerpFillError):
        sign_of(direction)


@pytest.mark.parametrize("direction", ["Liquidation", "open long", ""])
def test_unknown_dir_raises(direction: str) -> None:
    with pytest.raises(AdapterError) as info:
        sign_of(direction)
    assert not isinstance(info.value, NonPerpFillError)


def test_signed_size_and_notional() -> None:
    assert signed_size(make_fill(dir="Open Long", sz=D(2))) == Decimal(2)
    assert signed_size(make_fill(dir="Close Long", sz=D(2))) == Decimal(-2)
    assert signed_notional(make_fill(dir="Open Short", sz=D(2), px=D("1.5"))) == -3.0


def test_flip_signs() -> None:
    assert signed_size(make_fill(dir="Long > Short", sz=D(5))) < 0
    assert signed_size(make_fill(dir="Short > Long", sz=D(5))) > 0


def test_zero_size_is_zero() -> None:
    assert signed_notional(make_fill(sz=D(0))) == 0.0


def test_side_disagreeing_with_dir_is_rejected() -> None:
    with pytest.raises(AdapterError, match="side"):
        signed_size(make_fill(dir="Open Long", side=Side.SELL))


@given(
    st.sampled_from(sorted(EXPECTED_SIGN)),
    st.decimals(min_value=0, max_value=10**6, places=3),
    st.decimals(min_value="0.001", max_value=10**6, places=3),
)
def test_mirroring_flips_sign_and_notional_magnitude_is_px_times_sz(
    direction: str, sz: Decimal, px: Decimal
) -> None:
    fill = make_fill(dir=direction, sz=sz, px=px)
    assert abs(signed_notional(fill)) == pytest.approx(float(px * sz))
    assert signed_size(mirror(fill)) == -signed_size(fill)


@pytest.mark.parametrize(
    ("direction", "sign", "side"),
    [
        ("Liquidated Isolated Long", -1, Side.SELL),  # observed live 2026-09-24
        ("Liquidated Isolated Short", 1, Side.BUY),
        ("Liquidated Cross Long", -1, Side.SELL),
        ("Liquidated Cross Short", 1, Side.BUY),
    ],
)
def test_liquidations_close_the_position_with_a_forced_trade(
    direction: str, sign: int, side: Side
) -> None:
    assert is_perp_dir(direction)
    assert sign_of(direction) == sign
    fill = make_fill(dir=direction, side=side, sz=D(2))
    assert signed_size(fill) == sign * D(2)


def test_liquidation_side_is_still_cross_checked() -> None:
    with pytest.raises(AdapterError, match="side"):
        signed_size(make_fill(dir="Liquidated Isolated Long", side=Side.BUY))


@pytest.mark.parametrize(
    "direction", ["Liquidated Long", "Liquidated Isolated Flat", "Liquidated Portfolio Long"]
)
def test_other_liquidation_shapes_stay_unknown(direction: str) -> None:
    with pytest.raises(AdapterError, match="unknown"):
        sign_of(direction)


# --- Settlement: a delisted market's forced full close (sign follows the position) ------------


def test_real_settlement_fill_closes_the_short_it_settles() -> None:
    fill = adapt_fills([CATALOG[SETTLEMENT]], "0x" + "1" * 40)[0]  # IP, start -937.5, buy 937.5
    assert is_perp_dir(SETTLEMENT)
    assert signed_size(fill) == Decimal("937.5")
    assert fill.start_position + signed_size(fill) == 0


@pytest.mark.parametrize(("start", "side", "sz"), [(5, Side.SELL, 5), (-5, Side.BUY, 5)])
def test_settlement_closes_either_side(start: int, side: Side, sz: int) -> None:
    fill = make_fill(dir=SETTLEMENT, side=side, sz=D(sz), start_position=D(start))
    assert signed_size(fill) == -D(start)


@pytest.mark.parametrize(
    ("start", "side", "sz"),
    [(5, Side.SELL, 3), (5, Side.BUY, 5), (0, Side.BUY, 1), (-5, Side.BUY, 6)],
    ids=["partial", "wrong side", "no position", "overshoot"],
)
def test_settlement_that_does_not_close_the_position_is_rejected(
    start: int, side: Side, sz: int
) -> None:
    fill = make_fill(dir=SETTLEMENT, side=side, sz=D(sz), start_position=D(start))
    with pytest.raises(AdapterError, match=r"^settlement fill does not close the position"):
        signed_size(fill)


def test_settlement_has_no_fixed_sign() -> None:
    for direction in (SETTLEMENT, AUTO_DELEVERAGING):
        with pytest.raises(AdapterError, match="depends on the position"):
            sign_of(direction)


# --- Auto-deleveraging: a forced reduction of a position (sign follows the position) -------------


def test_real_adl_fill_closes_the_long_it_deleverages() -> None:
    fill = adapt_fills([CATALOG[AUTO_DELEVERAGING]], "0x" + "1" * 40)[0]  # CASHCAT long 163454
    assert is_perp_dir(AUTO_DELEVERAGING)
    assert signed_size(fill) == Decimal("-163454.0")


@pytest.mark.parametrize(
    ("start", "side", "sz", "delta"),
    [(5, Side.SELL, 5, -5), (5, Side.SELL, 2, -2), (-5, Side.BUY, 3, 3)],
    ids=["full", "partial long", "partial short"],
)
def test_adl_reduces_either_side(start: int, side: Side, sz: int, delta: int) -> None:
    fill = make_fill(dir=AUTO_DELEVERAGING, side=side, sz=D(sz), start_position=D(start))
    assert signed_size(fill) == D(delta)


@pytest.mark.parametrize(
    ("start", "side", "sz"),
    [(5, Side.BUY, 1), (0, Side.SELL, 1), (5, Side.SELL, 6)],
    ids=["increases", "no position", "flips"],
)
def test_adl_that_does_not_reduce_the_position_is_rejected(start: int, side: Side, sz: int) -> None:
    fill = make_fill(dir=AUTO_DELEVERAGING, side=side, sz=D(sz), start_position=D(start))
    with pytest.raises(AdapterError, match=r"^auto-deleveraging fill does not reduce the position"):
        signed_size(fill)

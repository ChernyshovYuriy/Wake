from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hlsignals.core.errors import AdapterError, NonPerpFillError
from hlsignals.domain.direction import is_perp_dir, sign_of, signed_notional, signed_size
from hlsignals.domain.models import Side
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


def test_every_perp_dir_in_fixture_catalog_matches_side() -> None:
    catalog = load_fixture("dir_catalog")["response"]
    perp = {d: f for d, f in catalog.items() if is_perp_dir(d)}
    assert set(perp) == set(EXPECTED_SIGN)
    for direction, raw in perp.items():
        assert (sign_of(direction) > 0) == (raw["side"] == Side.BUY)


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

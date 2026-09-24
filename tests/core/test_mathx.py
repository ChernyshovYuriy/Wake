from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hlsignals.core.mathx import (
    clamp,
    half_life_decay,
    safe_div,
    shrinkage,
    wilson_lower_bound,
)

NON_FINITE = [math.nan, math.inf, -math.inf]
Z95 = 1.96


@pytest.mark.parametrize(
    ("x", "expected"), [(-1.0, 0.0), (0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (2.0, 1.0)]
)
def test_clamp(x: float, expected: float) -> None:
    assert clamp(x, 0.0, 1.0) == expected


def test_clamp_rejects_inverted_bounds() -> None:
    with pytest.raises(ValueError, match="lo"):
        clamp(0.5, 1.0, 0.0)


@pytest.mark.parametrize("bad", NON_FINITE)
def test_clamp_rejects_non_finite(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        clamp(bad, 0.0, 1.0)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [(1.0, 0.0, -9.0), (6.0, 3.0, 2.0), (-6.0, 3.0, -2.0), (1.0, -4.0, -0.25)],
)
def test_safe_div(a: float, b: float, expected: float) -> None:
    assert safe_div(a, b, default=-9.0) == expected


def test_safe_div_tiny_denominator_is_finite_or_default() -> None:
    assert safe_div(1e-300, 1e-300, default=0.0) == 1.0
    assert safe_div(1e300, 1e-300, default=7.0) == 7.0  # overflow -> default, never inf


@pytest.mark.parametrize("bad", NON_FINITE)
def test_safe_div_rejects_non_finite(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        safe_div(bad, 1.0, default=0.0)


def test_wilson_zero_samples() -> None:
    assert wilson_lower_bound(0, 0, Z95) == 0.0


def test_wilson_all_wins_below_one_all_losses_zero() -> None:
    assert 0.0 < wilson_lower_bound(10, 10, Z95) < 1.0
    assert wilson_lower_bound(0, 10, Z95) == 0.0


def test_wilson_known_value() -> None:
    assert wilson_lower_bound(8, 10, Z95) == pytest.approx(0.4902, abs=1e-4)


def test_wilson_larger_z_is_more_conservative() -> None:
    assert wilson_lower_bound(8, 10, 2.58) < wilson_lower_bound(8, 10, Z95)


@pytest.mark.parametrize(
    ("wins", "n", "z", "match"),
    [(11, 10, Z95, "wins"), (-1, 10, Z95, "wins"), (1, -1, Z95, "wins"), (1, 2, 0.0, "z must")],
)
def test_wilson_rejects_invalid(wins: int, n: int, z: float, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        wilson_lower_bound(wins, n, z)


@given(st.integers(0, 500), st.integers(0, 500))
def test_wilson_in_unit_interval(wins: int, extra: int) -> None:
    assert 0.0 <= wilson_lower_bound(wins, wins + extra, Z95) <= 1.0


def test_shrinkage() -> None:
    assert shrinkage(0, 10) == 0.0
    assert shrinkage(10, 10) == 0.5
    assert shrinkage(1_000_000, 10) > 0.9999


@pytest.mark.parametrize(
    ("n", "k", "match"), [(1, 0, "k must"), (1, -1, "k must"), (-1, 10, "n must")]
)
def test_shrinkage_rejects_invalid(n: int, k: float, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        shrinkage(n, k)


@given(st.integers(0, 10_000), st.floats(0.1, 1000))
def test_shrinkage_monotone_in_n(n: int, k: float) -> None:
    assert shrinkage(n, k) <= shrinkage(n + 1, k)


def test_half_life_decay() -> None:
    assert half_life_decay(0, 21) == 1.0
    assert half_life_decay(21, 21) == pytest.approx(0.5)
    assert half_life_decay(42, 21) == pytest.approx(0.25)


@pytest.mark.parametrize(
    ("age", "half_life", "match"),
    [
        (-1, 21, "age must"),
        (1, 0, "half-life must"),
        (1, -5, "half-life must"),
        (math.nan, 21, "finite"),
        (1, math.inf, "finite"),
    ],
)
def test_half_life_decay_rejects_invalid(age: float, half_life: float, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        half_life_decay(age, half_life)


@given(st.floats(0, 1000), st.floats(0, 1000), st.floats(0.1, 100))
def test_half_life_decay_non_increasing(a: float, b: float, hl: float) -> None:
    lo, hi = sorted((a, b))
    assert half_life_decay(hi, hl) <= half_life_decay(lo, hl)

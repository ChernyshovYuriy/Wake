from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hlsignals.domain.models import (
    SignalComponent,
    SignalDirection,
    SignalStatus,
    TickerSignal,
)
from hlsignals.domain.symbols import Symbol
from hlsignals.signals.combiner import WeightedCombiner
from hlsignals.signals.corroboration import Corroboration
from hlsignals.signals.ranker import rank
from tests.factories import (
    D,
    make_fill,
    make_market_ctx,
    make_position,
    make_scored_wallet,
    make_ticker_inputs,
    wallet_address,
)

A, B, C = (wallet_address(i) for i in (1, 2, 3))

# --- corroboration -------------------------------------------------------------------------

RULE = Corroboration(min_wallets=3, min_trust=0.4)


def test_n_minus_one_is_insufficient() -> None:
    wallets = [make_scored_wallet(address=a, trust=0.5) for a in (A, B)]
    result = RULE.check(
        make_ticker_inputs(wallets=wallets, positions=[make_position(wallet=a) for a in (A, B)])
    )
    assert not result.sufficient
    assert result.n_wallets == 2
    assert "2 trusted wallets < 3" in result.reason


def test_exactly_n_holders_or_traders_is_sufficient() -> None:
    wallets = [make_scored_wallet(address=a, trust=0.5) for a in (A, B, C)]
    inputs = make_ticker_inputs(
        wallets=wallets,
        positions=[make_position(wallet=A), make_position(wallet=B)],
        fills=[make_fill(wallet=C)],  # C traded in the window without holding now
    )
    result = RULE.check(inputs)
    assert result.sufficient
    assert result.wallets == frozenset({A, B, C})


def test_low_trust_and_flat_positions_do_not_count() -> None:
    wallets = [
        make_scored_wallet(address=A, trust=0.39),
        make_scored_wallet(address=B, trust=0.4),
        make_scored_wallet(address=C, trust=0.9),
    ]
    inputs = make_ticker_inputs(
        wallets=wallets,
        positions=[
            make_position(wallet=A),
            make_position(wallet=B),
            make_position(wallet=C, size=D(0)),
        ],
    )
    assert RULE.check(inputs).wallets == frozenset({B})


def test_same_wallet_twice_counts_once() -> None:
    wallets = [make_scored_wallet(address=A, trust=0.5)]
    inputs = make_ticker_inputs(
        wallets=wallets, positions=[make_position(wallet=A)], fills=[make_fill(wallet=A)] * 2
    )
    assert RULE.check(inputs).n_wallets == 1


def test_corroboration_validation() -> None:
    with pytest.raises(ValueError, match="min_wallets"):
        Corroboration(0, 0.4)
    with pytest.raises(ValueError, match="min_trust"):
        Corroboration(3, 1.5)


# --- combiner -------------------------------------------------------------------------------

COMBINER = WeightedCombiner({"tilt": 1.0, "flow": 1.0, "overnight": 0.5}, epsilon=0.05)


def parts(tilt: float, flow: float, overnight: float) -> dict[str, SignalComponent]:
    return {
        "tilt": SignalComponent(tilt),
        "flow": SignalComponent(flow),
        "overnight": SignalComponent(overnight),
    }


def test_combiner_normalized_weighted_mean() -> None:
    score, direction = COMBINER.combine(parts(1.0, 0.5, -1.0))
    assert score == pytest.approx((1.0 + 0.5 - 0.5) / 2.5)
    assert direction is SignalDirection.LONG


def test_zero_score_is_flat() -> None:
    assert COMBINER.combine(parts(0.0, 0.0, 0.0)) == (0.0, SignalDirection.FLAT)


def test_score_exactly_epsilon_is_flat() -> None:
    combiner = WeightedCombiner({"tilt": 1.0}, epsilon=0.25)
    assert combiner.combine({"tilt": SignalComponent(0.25)})[1] is SignalDirection.FLAT
    assert combiner.combine({"tilt": SignalComponent(-0.25)})[1] is SignalDirection.FLAT
    assert combiner.combine({"tilt": SignalComponent(-0.2501)})[1] is SignalDirection.SHORT


def test_combiner_requires_every_weighted_component() -> None:
    with pytest.raises(ValueError, match="missing"):
        COMBINER.combine({"tilt": SignalComponent(1.0)})


@pytest.mark.parametrize(
    ("weights", "epsilon", "match"),
    [
        ({"tilt": -1.0}, 0.05, "negative"),
        ({"tilt": 0.0}, 0.05, "all signal weights are zero"),
        ({}, 0.05, "at least one"),
        ({"tilt": 1.0}, -0.1, "epsilon"),
        ({"tilt": 1.0}, 1.0, "epsilon"),
    ],
)
def test_combiner_validation(weights: dict[str, float], epsilon: float, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        WeightedCombiner(weights, epsilon)


unit = st.floats(-1.0, 1.0, allow_nan=False)


@given(unit, unit, unit)
def test_combiner_sign_symmetry_and_bounds(t: float, f: float, o: float) -> None:
    score, direction = COMBINER.combine(parts(t, f, o))
    mirrored, mirrored_direction = COMBINER.combine(parts(-t, -f, -o))
    assert -1.0 <= score <= 1.0
    assert mirrored == pytest.approx(-score)
    flipped = {
        SignalDirection.LONG: SignalDirection.SHORT,
        SignalDirection.SHORT: SignalDirection.LONG,
        SignalDirection.FLAT: SignalDirection.FLAT,
    }
    assert mirrored_direction is flipped[direction]


# --- ranker -----------------------------------------------------------------------------------


def signal(coin: str, score: float | None) -> TickerSignal:
    scored = score is not None
    return TickerSignal(
        symbol=Symbol("xyz", coin),
        status=SignalStatus.SCORED if scored else SignalStatus.INSUFFICIENT,
        direction=(SignalDirection.LONG if (score or 0) > 0 else SignalDirection.SHORT)
        if scored
        else None,
        score=score,
        components={},
        n_wallets=3,
        reason="",
        flags=frozenset(),
        market=make_market_ctx(),
    )


def test_rank_by_abs_score_ties_by_ticker_insufficient_last() -> None:
    signals = [
        signal("ZZZ", None),
        signal("AAA", 0.2),
        signal("MMM", -0.9),
        signal("BBB", 0.2),
        signal("CCC", None),
    ]
    assert [s.symbol.coin for s in rank(signals)] == ["MMM", "AAA", "BBB", "CCC", "ZZZ"]


def test_rank_empty() -> None:
    assert rank([]) == []

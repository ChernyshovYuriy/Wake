from __future__ import annotations

from collections.abc import Callable

import pytest

from hlsignals.core.clock import MS_PER_DAY
from hlsignals.wallets.scoring.features import (
    ConsistencyFeature,
    DrawdownFeature,
    HitRateFeature,
    HorizonFitFeature,
    PriorScoreFeature,
    WalletFeature,
)
from tests.factories import make_equity_slice, make_trips

EMPTY = make_equity_slice([])
FEATURES: list[WalletFeature] = [
    HitRateFeature(z=1.96),
    DrawdownFeature(max_tolerated_dd=0.5),
    ConsistencyFeature(period_days=7.0),
    HorizonFitFeature(horizon_days=5.0),
]


@pytest.mark.parametrize("feature", FEATURES, ids=lambda f: f.name)
def test_zero_trips_scores_zero_with_evidence(feature: WalletFeature) -> None:
    value = feature.compute(EMPTY)
    assert value is not None
    assert value.value == 0.0
    assert value.evidence["n_trips"] == 0


def test_hit_rate() -> None:
    all_wins = HitRateFeature(1.96).compute(make_equity_slice(make_trips([0.01] * 10)))
    all_losses = HitRateFeature(1.96).compute(make_equity_slice(make_trips([-0.01] * 10)))
    one = HitRateFeature(1.96).compute(make_equity_slice(make_trips([0.01])))
    assert 0.6 < all_wins.value < 1.0
    assert all_losses.value == 0.0
    assert one.value < all_wins.value  # one lucky trip earns little
    assert all_wins.evidence == {"wins": 10, "n_trips": 10, "z": 1.96, "raw_hit_rate": 1.0}


def test_drawdown() -> None:
    no_dd = DrawdownFeature(0.5).compute(make_equity_slice(make_trips([0.02, 0.03])))
    # cumulative returns 0.10, -0.10, 0.00 -> peak 0.10, trough -0.10: drawdown 0.20
    some_dd = DrawdownFeature(0.5).compute(make_equity_slice(make_trips([0.1, -0.2, 0.1])))
    wiped = DrawdownFeature(0.5).compute(make_equity_slice(make_trips([-0.6])))
    assert no_dd.value == 1.0
    assert some_dd.value == pytest.approx(1 - 0.2 / 0.5)
    assert some_dd.evidence["max_drawdown"] == pytest.approx(0.2)
    assert wiped.value == 0.0


def test_consistency_counts_positive_periods() -> None:
    # One trip per ~2 days over 4 weeks; weeks alternate winning / losing.
    returns = [0.01] * 4 + [-0.01] * 4 + [0.01] * 4 + [-0.02] * 4
    value = ConsistencyFeature(7.0).compute(
        make_equity_slice(
            make_trips(returns, hold_ms=MS_PER_DAY // 4, gap_ms=MS_PER_DAY + MS_PER_DAY * 3 // 4)
        )
    )
    assert value is not None
    assert 0.0 < value.value < 1.0
    counts = [
        value.evidence[k] for k in ("positive_periods", "negative_or_flat_periods", "periods")
    ]
    positive, other, periods = (c if isinstance(c, int) else -1 for c in counts)
    assert positive + other == periods > 0


@pytest.mark.parametrize(("hold_days", "expected"), [(5.0, 1.0), (2.5, 0.5), (10.0, 0.5)])
def test_horizon_fit(hold_days: float, expected: float) -> None:
    fills = make_trips([0.01] * 3, hold_ms=int(hold_days * MS_PER_DAY))
    value = HorizonFitFeature(5.0).compute(make_equity_slice(fills))
    assert value is not None
    assert value.value == pytest.approx(expected)


def test_prior_score() -> None:
    assert PriorScoreFeature(scale=100.0).compute(EMPTY) is None  # absent -> skipped
    with_score = make_equity_slice([], raw_score=80.0)
    capped = make_equity_slice([], raw_score=250.0)
    value, top = (
        PriorScoreFeature(100.0).compute(with_score),
        PriorScoreFeature(100.0).compute(capped),
    )
    assert value is not None
    assert top is not None
    assert value.value == 0.8
    assert top.value == 1.0


@pytest.mark.parametrize(
    "build",
    [
        lambda: HitRateFeature(0.0),
        lambda: DrawdownFeature(0.0),
        lambda: ConsistencyFeature(0.0),
        lambda: HorizonFitFeature(0.0),
        lambda: PriorScoreFeature(0.0),
    ],
)
def test_parameters_must_be_positive(build: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match="positive"):
        build()

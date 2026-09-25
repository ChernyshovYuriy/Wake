from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hlsignals.core.clock import MS_PER_DAY
from hlsignals.wallets.scoring.features import (
    DrawdownFeature,
    HitRateFeature,
    PriorScoreFeature,
)
from hlsignals.wallets.scoring.scorer import WalletScorer, WeightedFeature
from tests.factories import make_equity_slice, make_trips

HIT = WeightedFeature(HitRateFeature(1.96), 1.0)
DD = WeightedFeature(DrawdownFeature(0.5), 1.0)
PRIOR = WeightedFeature(PriorScoreFeature(100.0), 1.0)
SCORER = WalletScorer([HIT, DD, PRIOR], shrinkage_k=10.0, half_life_days=21.0)


def test_trust_formula_and_evidence() -> None:
    s = make_equity_slice(make_trips([0.02] * 10), raw_score=50.0)
    scored = SCORER.score(s)
    hit = scored.features["hit_rate"].value
    dd = scored.features["drawdown"].value
    prior = scored.features["prior_score"].value
    expected_base = (hit + dd + prior) / 3
    assert scored.confidence == pytest.approx(10 / 20)
    assert scored.decay == pytest.approx(0.5 ** (1 / 21))  # last fill one day before as_of
    assert scored.trust == pytest.approx(expected_base * scored.confidence * scored.decay)
    assert set(scored.features) == {"hit_rate", "drawdown", "prior_score"}
    assert scored.features["hit_rate"].evidence["weight"] == 1.0
    assert scored.n_closed_lots == 10
    assert scored.track_record_days == pytest.approx(19.0)


def test_absent_prior_is_skipped_and_weights_renormalize() -> None:
    s = make_equity_slice(make_trips([0.02] * 10))
    scored = SCORER.score(s)
    assert "prior_score" not in scored.features
    base = (scored.features["hit_rate"].value + scored.features["drawdown"].value) / 2
    assert scored.trust == pytest.approx(base * scored.confidence * scored.decay)


def test_empty_history_scores_zero() -> None:
    scored = SCORER.score(make_equity_slice([]))
    assert (scored.trust, scored.confidence, scored.decay) == (0.0, 0.0, 0.0)


def test_only_zero_weight_features_present_gives_zero_trust() -> None:
    scorer = WalletScorer([WeightedFeature(HitRateFeature(1.96), 0.0), PRIOR], 10.0, 21.0)
    assert scorer.score(make_equity_slice(make_trips([0.02] * 5))).trust == 0.0


@pytest.mark.parametrize(
    ("features", "k", "half_life", "match"),
    [
        (
            [WeightedFeature(HitRateFeature(1.96), 0.0)],
            10.0,
            21.0,
            "^all feature weights are zero$",
        ),
        (
            [WeightedFeature(HitRateFeature(1.96), -1.0)],
            10.0,
            21.0,
            "^feature weights must not be negative$",
        ),
        ([], 10.0, 21.0, "^a wallet scorer needs at least one feature$"),
        ([HIT, HIT], 10.0, 21.0, "^duplicate wallet features"),
        ([HIT], 0.0, 21.0, "^shrinkage_k must be positive: 0.0$"),
        ([HIT], 10.0, 0.0, "^half_life_days must be positive: 0.0$"),
    ],
)
def test_scorer_validation(
    features: list[WeightedFeature], k: float, half_life: float, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        WalletScorer(features, k, half_life)


def test_sub_unit_shrinkage_and_half_life_are_valid() -> None:
    scorer = WalletScorer([HIT], shrinkage_k=0.5, half_life_days=0.5)
    assert scorer.score(make_equity_slice(make_trips([0.02] * 3))).confidence == pytest.approx(
        3 / 3.5
    )


def test_a_skipped_feature_does_not_stop_the_ones_after_it() -> None:
    """prior_score (absent here) listed first: hit rate and drawdown must still be scored."""
    scorer = WalletScorer([PRIOR, HIT, DD], shrinkage_k=10.0, half_life_days=21.0)
    scored = scorer.score(make_equity_slice(make_trips([0.02] * 10)))
    assert set(scored.features) == {"hit_rate", "drawdown"}
    assert scored.trust > 0


returns = st.lists(st.floats(-0.5, 0.5, allow_nan=False), min_size=0, max_size=25)


@settings(deadline=None)
@given(returns, st.floats(0, 100) | st.none())
def test_trust_in_unit_interval(rs: list[float], prior: float | None) -> None:
    scored = SCORER.score(make_equity_slice(make_trips(rs), raw_score=prior))
    assert 0.0 <= scored.trust <= 1.0


@settings(deadline=None)
@given(st.integers(0, 20), st.integers(1, 20))
def test_confidence_non_decreasing_in_sample_size(n: int, extra: int) -> None:
    fewer = SCORER.score(make_equity_slice(make_trips([0.01] * n)))
    more = SCORER.score(make_equity_slice(make_trips([0.01] * (n + extra))))
    assert fewer.confidence <= more.confidence


@given(st.floats(0, 200), st.floats(0, 200))
def test_decay_non_increasing_in_age(a: float, b: float) -> None:
    fills = make_trips([0.01] * 5)
    last = fills[-1].time_ms
    young, old = sorted((a, b))
    s_young = make_equity_slice(fills, as_of_ms=last + int(young * MS_PER_DAY))
    s_old = make_equity_slice(fills, as_of_ms=last + int(old * MS_PER_DAY))
    assert SCORER.score(s_old).decay <= SCORER.score(s_young).decay

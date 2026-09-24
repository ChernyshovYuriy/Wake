from __future__ import annotations

import pytest

from hlsignals.universe.market_filters import (
    DelistedFilter,
    MinDayVolumeFilter,
    MinOpenInterestFilter,
    WatchlistFilter,
    market_filter_chain,
)
from tests.factories import AAPL, make_market_ctx


def test_delisted() -> None:
    f = DelistedFilter()
    assert f.apply(make_market_ctx()).accepted
    verdict = f.apply(make_market_ctx(is_delisted=True))
    assert not verdict.accepted
    assert "delisted" in verdict.reason


@pytest.mark.parametrize(
    ("volume", "accepted"), [(999_999.0, False), (1_000_000.0, True), (2e6, True)]
)
def test_min_day_volume_threshold_inclusive(volume: float, accepted: bool) -> None:
    verdict = MinDayVolumeFilter(1_000_000.0).apply(make_market_ctx(day_ntl_vlm=volume))
    assert verdict.accepted is accepted
    if not accepted:
        assert "999,999" in verdict.reason


@pytest.mark.parametrize(("oi", "accepted"), [(2_499.0, False), (2_500.0, True)])
def test_min_open_interest_uses_usd(oi: float, accepted: bool) -> None:
    # 2,500 contracts x $100 mark = $250,000
    verdict = MinOpenInterestFilter(250_000.0).apply(
        make_market_ctx(open_interest=oi, mark_px=100.0)
    )
    assert verdict.accepted is accepted


def test_watchlist() -> None:
    f = WatchlistFilter(frozenset({"NVDA"}))
    assert f.apply(make_market_ctx()).accepted
    assert not f.apply(make_market_ctx(symbol=AAPL)).accepted


def test_negative_thresholds_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        MinDayVolumeFilter(-1.0)
    with pytest.raises(ValueError, match="non-negative"):
        MinOpenInterestFilter(-1.0)


def test_empty_universe_after_filters_is_reported() -> None:
    chain = market_filter_chain([DelistedFilter(), MinDayVolumeFilter(1e12)])
    result = chain.run([make_market_ctx(), make_market_ctx(is_delisted=True)])
    assert result.is_empty
    assert result.counts_by_filter() == {"delisted": 1, "min_day_volume": 1}

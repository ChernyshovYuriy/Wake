from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from hlsignals.core.errors import AdapterError
from hlsignals.domain.models import FeatureValue, PositionSide
from tests.factories import (
    AS_OF,
    D,
    make_candle_series,
    make_fill,
    make_market_ctx,
    make_position,
    make_scored_wallet,
    make_wallet_record,
)

NAIVE = datetime(2026, 9, 24)  # noqa: DTZ001 - deliberately naive


def test_fill_notional_and_dedupe_key() -> None:
    fill = make_fill(px=D("2.5"), sz=D(4), tid=7, hash="0xabc")
    assert fill.notional == D(10)
    assert fill.dedupe_key == (7, "0xabc")


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"wallet": "0xNOPE"}, AdapterError),
        ({"px": D(0)}, ValueError),
        ({"sz": D(-1)}, ValueError),
        ({"time_ms": -1}, ValueError),
    ],
)
def test_fill_validation(overrides: dict[str, Any], error: type[Exception]) -> None:
    with pytest.raises(error):
        make_fill(**overrides)


@pytest.mark.parametrize(
    ("size", "side"), [(D(3), PositionSide.LONG), (D(-3), PositionSide.SHORT), (D(0), None)]
)
def test_position_side(size: object, side: PositionSide | None) -> None:
    assert make_position(size=size).side is side


def test_position_validation() -> None:
    with pytest.raises(ValueError, match="entry"):
        make_position(entry_px=D(0))


def test_candle_series_is_valid_and_chained() -> None:
    candles = make_candle_series([100.0, 105.0, 95.0])
    assert [c.open for c in candles] == [100.0, 100.0, 105.0]
    assert candles[2].low == 95.0


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"close_ms": 0}, "closes before"),
        ({"low": 0.0}, "candle range"),
        ({"high": 50.0}, "candle range"),
        ({"close": 1000.0}, "outside low"),
        ({"volume": -1.0}, "negative candle volume"),
        ({"n_trades": -1}, "negative candle volume"),
    ],
)
def test_candle_validation(overrides: dict[str, Any], match: str) -> None:
    (candle,) = make_candle_series([100.0])
    with pytest.raises(ValueError, match=match):
        replace(candle, **overrides)


def test_market_ctx_oi_usd() -> None:
    assert make_market_ctx(open_interest=10.0, mark_px=250.0).oi_usd == 2500.0


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"mark_px": 0.0}, "prices must be positive"),
        ({"oracle_px": -1.0}, "prices must be positive"),
        ({"open_interest": -1.0}, "negative open interest"),
        ({"day_ntl_vlm": -1.0}, "negative volume"),
    ],
)
def test_market_ctx_validation(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        make_market_ctx(**overrides)


def test_wallet_record_accepts_optional_score() -> None:
    assert make_wallet_record(raw_score=3.5).raw_score == 3.5
    assert make_wallet_record().raw_score is None


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"address": "bad"}, AdapterError),
        ({"as_of": NAIVE}, ValueError),
        ({"raw_score": -1.0}, ValueError),
        ({"raw_score": math.nan}, ValueError),
    ],
)
def test_wallet_record_validation(overrides: dict[str, Any], error: type[Exception]) -> None:
    with pytest.raises(error):
        make_wallet_record(**overrides)


def test_feature_value_evidence_is_read_only_copy() -> None:
    source = {"wins": 3}
    feature = FeatureValue(0.5, source)
    source["wins"] = 99
    assert feature.evidence["wins"] == 3
    with pytest.raises(TypeError):
        feature.evidence["wins"] = 1  # type: ignore[index]


@pytest.mark.parametrize("bad", [-0.1, 1.1, math.nan])
def test_feature_value_must_be_unit(bad: float) -> None:
    with pytest.raises(ValueError, match="feature value"):
        FeatureValue(bad)


def test_scored_wallet_valid_and_features_frozen() -> None:
    wallet = make_scored_wallet()
    with pytest.raises(TypeError):
        wallet.features["x"] = FeatureValue(0.1)  # type: ignore[index]


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"address": "bad"}, AdapterError),
        ({"trust": 1.5}, ValueError),
        ({"confidence": -0.1}, ValueError),
        ({"decay": math.nan}, ValueError),
        ({"n_closed_lots": -1}, ValueError),
        ({"track_record_days": -1.0}, ValueError),
    ],
)
def test_scored_wallet_validation(overrides: dict[str, Any], error: type[Exception]) -> None:
    with pytest.raises(error):
        make_scored_wallet(**overrides)


def test_as_of_constant_is_aware() -> None:
    assert AS_OF.tzinfo is not None

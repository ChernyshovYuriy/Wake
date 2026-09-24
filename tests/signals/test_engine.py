from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hlsignals.core.clock import MS_PER_HOUR
from hlsignals.domain.models import Candle, SignalDirection, SignalFlag, SignalStatus
from hlsignals.domain.symbols import Symbol
from hlsignals.signals.combiner import WeightedCombiner
from hlsignals.signals.corroboration import Corroboration
from hlsignals.signals.engine import FlagThresholds, SignalEngine
from hlsignals.signals.features import FlowFeature, OvernightFeature, PositioningFeature
from hlsignals.signals.inputs import TickerInputs
from tests.factories import (
    AAPL,
    T0_MS,
    D,
    make_candle_series,
    make_fill,
    make_market_ctx,
    make_position,
    make_scored_wallet,
    make_ticker_inputs,
    mirror,
    wallet_address,
)

WALLETS = [
    make_scored_wallet(address=wallet_address(i), trust=0.6, confidence=0.8) for i in range(4)
]
AS_OF = T0_MS + 48 * MS_PER_HOUR
CLOSE = T0_MS + 40 * MS_PER_HOUR

ENGINE = SignalEngine(
    features=[
        PositioningFeature(),
        FlowFeature(window_hours=24.0, min_oi_frac=0.02, full_scale_oi_frac=0.10),
        OvernightFeature(min_move=0.003, full_scale_move=0.03, max_staleness_hours=2.0),
    ],
    corroboration=Corroboration(min_wallets=3, min_trust=0.4),
    combiner=WeightedCombiner({"tilt": 1.0, "flow": 1.0, "overnight": 0.5}, epsilon=0.05),
    flags=FlagThresholds(thin_volume_usd=5_000_000.0, weak_confidence=0.5),
)


def rising_candles(n: int = 48) -> list[Candle]:
    return make_candle_series([100.0 + 0.1 * i for i in range(n)], step_ms=MS_PER_HOUR)


def bullish_inputs(**overrides: Any) -> TickerInputs:
    base: dict[str, Any] = {
        "wallets": WALLETS,
        "positions": [make_position(wallet=w.address, size=D(50)) for w in WALLETS[:3]],
        "fills": [make_fill(wallet=WALLETS[0].address, sz=D(500), time_ms=AS_OF - MS_PER_HOUR)],
        "candles": rising_candles(),
        "as_of_ms": AS_OF,
        "last_close_ms": CLOSE,
    }
    return make_ticker_inputs(**{**base, **overrides})


def test_scored_long_signal_with_full_evidence() -> None:
    signal = ENGINE.evaluate(bullish_inputs())
    assert signal.status is SignalStatus.SCORED
    assert signal.direction is SignalDirection.LONG
    assert signal.score is not None
    assert signal.score > 0.05
    assert set(signal.components) == {"tilt", "flow", "overnight"}
    assert signal.n_wallets == 3
    assert signal.flags == frozenset()
    assert "long" in signal.reason


def test_insufficient_corroboration_has_no_score() -> None:
    signal = ENGINE.evaluate(
        bullish_inputs(positions=[make_position(wallet=WALLETS[0].address)], fills=[])
    )
    assert signal.status is SignalStatus.INSUFFICIENT
    assert signal.score is None
    assert signal.direction is None
    assert "1 trusted wallets < 3" in signal.reason
    assert set(signal.components) == {"tilt", "flow", "overnight"}  # still explained


def test_flags() -> None:
    weak = [replace(w, confidence=0.3) for w in WALLETS]
    signal = ENGINE.evaluate(
        bullish_inputs(
            wallets=weak,
            positions=[make_position(wallet=w.address) for w in weak[:3]],
            market=make_market_ctx(day_ntl_vlm=1_000_000.0),
            session_open=True,
            as_of_ms=AS_OF + 10 * MS_PER_HOUR,
        )
    )
    assert signal.flags == {
        SignalFlag.THIN_VOLUME,
        SignalFlag.WEAK_SAMPLE,
        SignalFlag.STALE_OVERNIGHT_REF,
        SignalFlag.CASH_SESSION_OPEN,
    }


def test_engine_rejects_weights_for_features_it_does_not_compute() -> None:
    with pytest.raises(ValueError, match="not computed"):
        SignalEngine([PositioningFeature()], ENGINE.corroboration, ENGINE.combiner, ENGINE.flags)


def test_engine_rejects_duplicate_feature_names() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        SignalEngine(
            [PositioningFeature(), PositioningFeature()],
            ENGINE.corroboration,
            ENGINE.combiner,
            ENGINE.flags,
        )


# --- input validation ---------------------------------------------------------------------------


def test_inputs_reject_unknown_wallets_and_other_symbols() -> None:
    with pytest.raises(ValueError, match="unscored wallet"):
        make_ticker_inputs(positions=[make_position()])
    with pytest.raises(ValueError, match="unscored wallet"):
        make_ticker_inputs(fills=[make_fill()])
    with pytest.raises(ValueError, match="symbol"):
        make_ticker_inputs(
            wallets=WALLETS[:1], positions=[make_position(wallet=WALLETS[0].address, symbol=AAPL)]
        )
    with pytest.raises(ValueError, match="symbol"):
        make_ticker_inputs(market=make_market_ctx(symbol=AAPL))
    with pytest.raises(ValueError, match="close"):
        make_ticker_inputs(as_of_ms=T0_MS, last_close_ms=T0_MS + 1)


# --- properties ---------------------------------------------------------------------------


def mirror_inputs(inputs: TickerInputs) -> TickerInputs:
    """Every position and fill reversed; the price path inverted (p -> 10_000 / p)."""
    candles = [
        replace(
            c,
            open=10_000 / c.open,
            close=10_000 / c.close,
            high=10_000 / c.low,
            low=10_000 / c.high,
        )
        for c in inputs.candles
    ]
    return replace(
        inputs,
        positions=tuple(replace(p, size=-p.size) for p in inputs.positions),
        fills=tuple(mirror(f) for f in inputs.fills),
        candles=tuple(candles),
    )


sizes = st.integers(-100, 100)
closes = st.lists(st.floats(50.0, 150.0, allow_nan=False), min_size=0, max_size=48)


@settings(deadline=None, max_examples=60)
@given(
    st.lists(sizes, min_size=0, max_size=4),
    st.lists(st.tuples(st.integers(0, 3), st.integers(1, 2000)), max_size=5),
    closes,
)
def test_bounds_and_sign_symmetry(
    pos_sizes: list[int], trades: list[tuple[int, int]], path: list[float]
) -> None:
    positions = [
        make_position(wallet=WALLETS[i].address, size=D(s)) for i, s in enumerate(pos_sizes)
    ]
    fills = [
        make_fill(wallet=WALLETS[i].address, sz=D(sz), time_ms=AS_OF - MS_PER_HOUR * (k + 1))
        for k, (i, sz) in enumerate(trades)
    ]
    inputs = bullish_inputs(
        positions=positions, fills=fills, candles=make_candle_series(path, step_ms=MS_PER_HOUR)
    )
    signal, mirrored = ENGINE.evaluate(inputs), ENGINE.evaluate(mirror_inputs(inputs))

    for component in signal.components.values():
        assert -1.0 <= component.value <= 1.0
    assert signal.status is mirrored.status
    assert signal.flags == mirrored.flags
    for name, component in signal.components.items():
        assert mirrored.components[name].value == pytest.approx(-component.value, abs=1e-9)
    if signal.score is not None:
        assert -1.0 <= signal.score <= 1.0
        assert mirrored.score == pytest.approx(-signal.score, abs=1e-9)


def test_symbol_of_default_inputs() -> None:
    assert make_ticker_inputs().symbol == Symbol("xyz", "NVDA")

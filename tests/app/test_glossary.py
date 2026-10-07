from __future__ import annotations

from hlsignals.app.config import SignalSettings
from hlsignals.app.glossary import build_glossary
from hlsignals.app.wiring import build_signal_engine
from hlsignals.core.clock import MS_PER_HOUR
from hlsignals.domain.models import SignalDirection, SignalFlag, SignalStatus
from tests.factories import T0_MS, make_candle_series, make_market_ctx, make_ticker_inputs

SETTINGS = SignalSettings()
GLOSSARY = build_glossary(SETTINGS)


def emitted_evidence_keys() -> dict[str, set[str]]:
    """Every evidence key the configured features emit, across their zero and non-zero paths."""
    as_of = T0_MS + 6 * MS_PER_HOUR
    inputs = (
        make_ticker_inputs(),  # no candles, no fills
        make_ticker_inputs(market=make_market_ctx(open_interest=0.0)),
        make_ticker_inputs(
            candles=make_candle_series([100.0] * 3 + [105.0] * 3),
            as_of_ms=as_of,
            last_close_ms=T0_MS + 3 * MS_PER_HOUR,
        ),
    )
    keys: dict[str, set[str]] = {}
    for feature in build_signal_engine(SETTINGS).features:
        for i in inputs:
            keys.setdefault(feature.name, set()).update(feature.compute(i).evidence)
    return keys


def test_every_emitted_evidence_key_is_explained() -> None:
    for component, keys in emitted_evidence_keys().items():
        assert component in GLOSSARY.columns
        assert keys <= set(GLOSSARY.evidence[component]), component


def test_every_enum_value_is_explained() -> None:
    assert set(GLOSSARY.flags) == set(SignalFlag)
    assert set(GLOSSARY.directions) == set(SignalDirection)
    assert set(GLOSSARY.statuses) == set(SignalStatus)


def test_tips_state_the_configured_thresholds() -> None:
    custom = build_glossary(
        SignalSettings(epsilon=0.07, short_epsilon=0.35, thin_volume_usd=1_234_567.0)
    )
    assert "+0.07" in custom.directions[SignalDirection.LONG]
    assert "-0.35" in custom.directions[SignalDirection.SHORT]
    assert "between -0.35 and +0.07" in custom.directions[SignalDirection.FLAT]
    assert "$1,234,567" in custom.flags[SignalFlag.THIN_VOLUME]
    assert "1 x tilt + 1 x flow + 0.5 x overnight) / 2.5" in GLOSSARY.columns["score"]

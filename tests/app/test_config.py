from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hlsignals.app.config import Settings, load_settings, settings_from_mapping
from hlsignals.app.wiring import validate_settings
from hlsignals.core.errors import ConfigError

ROOT = Path(__file__).parents[2]


def test_defaults_without_file_are_valid() -> None:
    settings = load_settings(None)
    assert settings == Settings()
    validate_settings(settings)


def test_example_config_loads_and_validates() -> None:
    settings = load_settings(ROOT / "config" / "example.toml")
    validate_settings(settings)
    assert settings.universe.include_classes == frozenset({"equity_us"})
    assert settings.wallet_sources.precedence == ("curated", "census")
    assert settings.wallet_filters.order[0] == "min_sample"


def test_overrides_are_typed_and_partial() -> None:
    settings = settings_from_mapping(
        {
            "signals": {"min_wallets": 4, "weights": {"tilt": 2, "flow": 1, "overnight": 0}},
            "universe": {"min_day_volume_usd": 2_000_000},
            "wallets": {
                "sources": [{"type": "curated", "path": "w.toml"}],
                "precedence": ["curated"],
                "scoring": {"half_life_days": 30},
            },
        }
    )
    assert settings.signals.min_wallets == 4
    assert settings.signals.weights["tilt"] == 2.0
    assert settings.signals.epsilon == Settings().signals.epsilon  # untouched default
    assert settings.universe.min_day_volume_usd == 2_000_000.0
    assert settings.scoring.half_life_days == 30.0
    assert settings.wallet_sources.sources[0]["path"] == "w.toml"


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ({"signal": {}}, "unknown config section"),
        ({"signals": {"min_walets": 3}}, r"\[signals\].*min_walets"),
        ({"signals": {"min_wallets": "3"}}, "signals.min_wallets must be int"),
        ({"signals": {"min_wallets": True}}, "signals.min_wallets must be int"),
        ({"signals": {"epsilon": "small"}}, "signals.epsilon must be a number"),
        ({"signals": {"weights": {"tilt": "x"}}}, r"signals.weights.tilt"),
        ({"signals": {"weights": [1]}}, "signals.weights must be a table"),
        ({"universe": {"dex_preference": "xyz"}}, "universe.dex_preference must be a list"),
        ({"universe": {"include_classes": [1]}}, r"include_classes\[\] must be str"),
        ({"wallets": {"sources": [1]}}, r"sources\[\] must be a table"),
        ({"wallets": {"colour": 1}}, r"\[wallets\].*colour"),
        ({"wallets": {"filters": 3}}, r"\[wallets.filters\] must be a table"),
        ({"census": {"nansen_api_key": "abc"}}, "secrets must come from environment"),
        ({"wallets": {"sources": [{"type": "nansen", "api_key": "abc"}]}}, "secrets"),
    ],
)
def test_invalid_config_names_the_field(raw: dict[str, Any], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        settings_from_mapping(raw)


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ({"signals": {"min_trust": 1.5}}, r"\[signals\].*min_trust"),
        ({"wallets": {"filters": {"min_taker_ratio": 2.0}}}, r"\[wallets.filters\]"),
        ({"wallets": {"scoring": {"shrinkage_k": 0}}}, r"\[wallets.scoring\].*shrinkage_k"),
        ({"universe": {"min_day_volume_usd": -1}}, r"\[universe\]"),
        ({"report": {"format": "html"}}, "report.format"),
        ({"wallets": {"sources": []}}, "at least one"),
    ],
)
def test_invalid_values_fail_validation(raw: dict[str, Any], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        validate_settings(settings_from_mapping(raw))


def test_missing_and_malformed_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[signals\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_settings(bad)


def test_secrets_never_appear_in_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NANSEN_API_KEY", "super-secret-value")
    assert "super-secret-value" not in repr(load_settings(ROOT / "config" / "example.toml"))

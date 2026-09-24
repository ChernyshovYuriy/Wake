from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from hlsignals.app.config import ScoringSettings, Settings, WalletFilterSettings
from hlsignals.app.wiring import (
    build_gateway,
    build_wallet_filter_chain,
    build_wallet_scorer,
    load_catalog,
    load_equities,
)
from hlsignals.core.clock import FakeClock, FakeSleeper
from hlsignals.core.errors import ConfigError
from hlsignals.domain.symbols import Symbol
from hlsignals.infra.gateway import HyperliquidGateway
from tests.factories import AS_OF, make_equity_slice, make_trips

ROOT = Path(__file__).parents[2]


def test_default_chain_runs_filters_in_configured_order() -> None:
    chain = build_wallet_filter_chain(WalletFilterSettings())
    rejection = chain.evaluate(make_equity_slice([]))
    assert rejection is not None
    assert rejection.filter_name == "min_sample"


def test_custom_order_and_subset() -> None:
    chain = build_wallet_filter_chain(WalletFilterSettings(order=("inactivity",)))
    rejection = chain.evaluate(make_equity_slice([]))
    assert rejection is not None
    assert rejection.filter_name == "inactivity"


def test_unknown_filter_name_lists_valid_ones() -> None:
    with pytest.raises(ConfigError, match=r"maker_profile.*min_sample"):
        build_wallet_filter_chain(WalletFilterSettings(order=("min_sample", "vibes")))


def test_default_scorer_scores() -> None:
    scored = build_wallet_scorer(ScoringSettings()).score(
        make_equity_slice(make_trips([0.01] * 12))
    )
    assert set(scored.features) == {"hit_rate", "drawdown", "consistency", "horizon_fit"}


def test_unknown_or_missing_feature_weight() -> None:
    bad = replace(ScoringSettings(), weights=MappingProxyType({"hit_rate": 1.0, "luck": 1.0}))
    with pytest.raises(ConfigError, match="luck"):
        build_wallet_scorer(bad)


def test_catalog_and_equities_from_repo_config() -> None:
    settings = Settings()
    catalog = load_catalog(ROOT / settings.universe.instruments_path)
    equities = load_equities(catalog, settings.universe.include_classes)
    assert Symbol("xyz", "NVDA") in equities
    assert Symbol("xyz", "SP500") not in equities


def test_load_catalog_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_catalog(tmp_path / "nope.toml")


def test_build_gateway_without_network() -> None:
    clock = FakeClock(AS_OF)
    gateway = build_gateway(Settings().api, clock, FakeSleeper(clock))
    assert isinstance(gateway, HyperliquidGateway)

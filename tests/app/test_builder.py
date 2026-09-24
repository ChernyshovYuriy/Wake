from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hlsignals.app.builder import PipelineBuilder
from hlsignals.app.config import Settings, settings_from_mapping
from hlsignals.core.clock import FakeClock
from hlsignals.core.errors import ConfigError
from hlsignals.infra.transport import FixtureTransport
from hlsignals.wallets.census.registry import InMemoryRegistry
from tests.conftest import FIXTURE_DIR
from tests.factories import AS_OF

ROOT = Path(__file__).parents[2]


def test_missing_parts_are_named() -> None:
    with pytest.raises(ConfigError, match="settings, clock, transport"):
        PipelineBuilder().build()
    with pytest.raises(ConfigError, match="missing: transport"):
        PipelineBuilder().with_settings(Settings()).with_clock(FakeClock(AS_OF)).build()


def test_invalid_settings_fail_at_build() -> None:
    bad = settings_from_mapping({"signals": {"min_trust": 2.0}})
    with pytest.raises(ConfigError, match="min_trust"):
        (
            PipelineBuilder()
            .with_settings(bad)
            .with_clock(FakeClock(AS_OF))
            .with_transport(FixtureTransport(FIXTURE_DIR))
            .with_base_dir(ROOT)
            .build()
        )


def test_full_build_with_fixtures_and_env() -> None:
    pipeline = (
        PipelineBuilder()
        .with_settings(Settings())
        .with_clock(FakeClock(AS_OF))
        .with_transport(FixtureTransport(FIXTURE_DIR))
        .with_env({})
        .with_registry(InMemoryRegistry())
        .with_base_dir(ROOT)
        .build()
    )
    result = pipeline.fetch_wallets()
    assert result.failed == ()  # the curated template is empty, the census is fresh
    assert "curated" in result.used


def test_default_registry_is_opened_under_base_dir(tmp_path: Path) -> None:
    settings = Settings()
    settings = dataclasses.replace(
        settings,
        universe=dataclasses.replace(
            settings.universe, instruments_path=str(ROOT / settings.universe.instruments_path)
        ),
        calendar=dataclasses.replace(settings.calendar, path=str(ROOT / settings.calendar.path)),
        wallet_sources=dataclasses.replace(
            settings.wallet_sources,
            sources=({"type": "census", "min_observations": 1},),
            precedence=("census",),
        ),
    )
    (
        PipelineBuilder()
        .with_settings(settings)
        .with_clock(FakeClock(AS_OF))
        .with_transport(FixtureTransport(FIXTURE_DIR))
        .with_base_dir(tmp_path)
        .build()
    )
    assert (tmp_path / "data" / "census.sqlite").exists()

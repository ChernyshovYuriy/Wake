"""PipelineBuilder: assembles a SignalPipeline from validated settings, step by step.

Required parts (settings, clock, transport) must be given; a missing one is a
ConfigError naming it. Optional parts default: env {}, the census registry opened from
settings, and relative paths resolved against the working directory.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from hlsignals.app.config import Settings
from hlsignals.app.pipeline import PipelineParts, SignalPipeline
from hlsignals.app.wiring import (
    build_market_filter_chain,
    build_signal_engine,
    build_wallet_filter_chain,
    build_wallet_scorer,
    gateway_for,
    load_calendar,
    load_catalog,
    load_equities,
    validate_settings,
)
from hlsignals.core.clock import Clock
from hlsignals.core.errors import ConfigError
from hlsignals.infra.transport import Transport
from hlsignals.universe.discovery import EquityDexLocator
from hlsignals.wallets.census.registry import SqliteRegistry, WalletRegistry
from hlsignals.wallets.sources.base import WalletSourcePort
from hlsignals.wallets.sources.composite import CompositeWalletSource
from hlsignals.wallets.sources.factory import SourceDeps, default_source_factory


def open_registry(settings: Settings, base_dir: Path) -> SqliteRegistry:
    path = base_dir / settings.census.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteRegistry(path)


class PipelineBuilder:
    def __init__(self) -> None:
        self._settings: Settings | None = None
        self._clock: Clock | None = None
        self._transport: Transport | None = None
        self._env: Mapping[str, str] = {}
        self._registry: WalletRegistry | None = None
        self._base_dir = Path()

    def with_settings(self, settings: Settings) -> PipelineBuilder:
        self._settings = settings
        return self

    def with_clock(self, clock: Clock) -> PipelineBuilder:
        self._clock = clock
        return self

    def with_transport(self, transport: Transport) -> PipelineBuilder:
        self._transport = transport
        return self

    def with_env(self, env: Mapping[str, str]) -> PipelineBuilder:
        self._env = env
        return self

    def with_registry(self, registry: WalletRegistry) -> PipelineBuilder:
        self._registry = registry
        return self

    def with_base_dir(self, base_dir: Path) -> PipelineBuilder:
        self._base_dir = base_dir
        return self

    def build(self) -> SignalPipeline:
        settings, clock, transport = self._settings, self._clock, self._transport
        if settings is None or clock is None or transport is None:
            missing = [
                name
                for name, part in (
                    ("settings", settings),
                    ("clock", clock),
                    ("transport", transport),
                )
                if part is None
            ]
            raise ConfigError(f"pipeline is missing: {', '.join(missing)}")
        validate_settings(settings)
        base = self._base_dir
        catalog = load_catalog(base / settings.universe.instruments_path)
        gateway = gateway_for(transport, settings.api)
        return SignalPipeline(
            PipelineParts(
                settings=settings,
                gateway=gateway,
                calendar=load_calendar(base / settings.calendar.path),
                locator=EquityDexLocator(
                    gateway,
                    catalog,
                    settings.universe.include_classes,
                    settings.universe.dex_preference,
                ),
                market_chain=build_market_filter_chain(settings.universe),
                wallet_source=self._configured_sources(settings, clock),
                equities=load_equities(catalog, settings.universe.include_classes),
                wallet_chain=build_wallet_filter_chain(settings.wallet_filters),
                scorer=build_wallet_scorer(settings.scoring),
                engine=build_signal_engine(settings.signals),
            )
        )

    def _configured_sources(self, settings: Settings, clock: Clock) -> WalletSourcePort:
        registry = self._registry or open_registry(settings, self._base_dir)
        deps = SourceDeps(clock=clock, env=self._env, registry=registry, base_dir=self._base_dir)
        factory = default_source_factory()
        sources = [factory.create(spec, deps) for spec in settings.wallet_sources.sources]
        return CompositeWalletSource(sources, precedence=settings.wallet_sources.precedence)

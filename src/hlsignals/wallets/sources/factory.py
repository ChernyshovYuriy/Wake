"""SourceFactory: builds wallet sources from config specs keyed by ``type``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hlsignals.core.clock import Clock
from hlsignals.core.errors import ConfigError
from hlsignals.wallets.census.registry import WalletRegistry
from hlsignals.wallets.sources.base import WalletSourcePort
from hlsignals.wallets.sources.census import CensusSource
from hlsignals.wallets.sources.curated import CuratedSource
from hlsignals.wallets.sources.paid import ApifySource, NansenSource


@dataclass(frozen=True, slots=True)
class SourceDeps:
    clock: Clock
    env: Mapping[str, str]
    registry: WalletRegistry
    base_dir: Path  # relative paths in specs resolve against this


SourceBuilder = Callable[[Mapping[str, Any], SourceDeps], WalletSourcePort]


class SourceFactory:
    def __init__(self, builders: Mapping[str, SourceBuilder]) -> None:
        self._builders = dict(builders)

    def register(self, kind: str, builder: SourceBuilder) -> None:
        if kind in self._builders:
            raise ConfigError(f"wallet source type {kind!r} is already registered")
        self._builders[kind] = builder

    def create(self, spec: Mapping[str, Any], deps: SourceDeps) -> WalletSourcePort:
        kind = _required(spec, "type", str)
        builder = self._builders.get(kind)
        if builder is None:
            raise ConfigError(
                f"unknown wallet source type {kind!r}; valid types: {sorted(self._builders)}"
            )
        return builder(spec, deps)


def _required[T](spec: Mapping[str, Any], key: str, kind: type[T]) -> T:
    if key not in spec:
        raise ConfigError(f"wallet source spec is missing {key!r}: {dict(spec)}")
    value = spec[key]
    if isinstance(value, bool) or not isinstance(value, kind):
        raise ConfigError(f"wallet source {key!r} must be {kind.__name__}, got {value!r}")
    return value


def _curated(spec: Mapping[str, Any], deps: SourceDeps) -> WalletSourcePort:
    return CuratedSource(deps.base_dir / _required(spec, "path", str), deps.clock)


def _census(spec: Mapping[str, Any], deps: SourceDeps) -> WalletSourcePort:
    return CensusSource(deps.registry, _required(spec, "min_observations", int))


def default_source_factory() -> SourceFactory:
    return SourceFactory(
        {
            "curated": _curated,
            "census": _census,
            "nansen": lambda spec, deps: NansenSource(deps.env, fetch=None),
            "apify": lambda spec, deps: ApifySource(deps.env, fetch=None),
        }
    )

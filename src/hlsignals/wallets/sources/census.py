"""CensusSource: wallets the census recorder has seen trading tracked symbols."""

from __future__ import annotations

import sqlite3
from typing import Any

from hlsignals.core.clock import from_ms
from hlsignals.core.errors import ConfigError, SourceError
from hlsignals.wallets.census.registry import WalletObservation, WalletRegistry
from hlsignals.wallets.sources.base import Loaded, RawWallet, WalletSource


class CensusSource(WalletSource):
    def __init__(
        self, registry: WalletRegistry, min_observations: int, name: str = "census"
    ) -> None:
        if min_observations < 1:
            raise ConfigError(f"census min_observations must be >= 1: {min_observations}")
        super().__init__(name)
        self._registry = registry
        self._min = min_observations

    def _load_raw(self) -> Loaded:
        try:
            return Loaded(self._registry.observations(min_fills=self._min))
        except sqlite3.Error as exc:
            raise SourceError(f"{self.name}: registry unavailable: {exc}") from exc

    def _adapt(self, item: Any) -> RawWallet:
        observation: WalletObservation = item
        return RawWallet(
            address=observation.address,
            as_of=from_ms(observation.last_seen_ms),
            raw_metric=f"census_fills={observation.n_fills}",
        )

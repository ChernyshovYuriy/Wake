"""CuratedSource: wallets listed by hand in a TOML file.

[[wallets]]
address = "0x..."
score = 87.5                      # optional, e.g. a Copy Score from Hyperdash/ASXN
metric = "hyperdash_copy_score"   # optional: what ``score`` measures
as_of = 2026-09-20T00:00:00Z      # optional: when the score was taken (default: now)
note = "free text"                # optional, ignored
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from hlsignals.core.clock import Clock, require_aware
from hlsignals.core.errors import AdapterError, SourceError
from hlsignals.wallets.sources.base import Loaded, RawWallet, WalletSource

_KEYS = frozenset({"address", "score", "metric", "as_of", "note"})


class CuratedSource(WalletSource):
    def __init__(self, path: Path, clock: Clock, name: str = "curated") -> None:
        super().__init__(name)
        self._path = path
        self._clock = clock

    def _load_raw(self) -> Loaded:
        try:
            doc = tomllib.loads(self._path.read_text())
        except FileNotFoundError as exc:
            raise SourceError(f"{self.name}: file not found: {self._path}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise SourceError(f"{self.name}: invalid TOML in {self._path}: {exc}") from exc
        wallets = doc.get("wallets", [])
        if not isinstance(wallets, list):
            raise SourceError(f"{self.name}: 'wallets' must be a list of tables")
        return Loaded(wallets)

    def _adapt(self, item: Any) -> RawWallet:
        if not isinstance(item, Mapping) or "address" not in item:
            raise AdapterError(f"entry without an address: {item!r}")
        unknown = set(item) - _KEYS
        if unknown:
            raise AdapterError(f"unknown key(s) {sorted(unknown)} in entry {item['address']}")
        score = item.get("score")
        if score is not None and (isinstance(score, bool) or not isinstance(score, int | float)):
            raise AdapterError(f"score must be a number, got {score!r}")
        as_of = item.get("as_of", self._clock.now())
        if not isinstance(as_of, datetime):
            raise AdapterError(f"as_of must be a datetime, got {as_of!r}")
        require_aware(as_of)
        metric = item.get("metric")
        return RawWallet(
            address=str(item["address"]),
            as_of=as_of,
            raw_score=None if score is None else float(score),
            raw_metric=None if metric is None else str(metric),
        )

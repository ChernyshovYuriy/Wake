"""Builders that turn a list of wallet entries into each WalletSource implementation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from hlsignals.core.clock import FakeClock, to_ms
from hlsignals.wallets.census.registry import InMemoryRegistry
from hlsignals.wallets.sources.base import RawWallet, WalletSourcePort
from hlsignals.wallets.sources.census import CensusSource
from hlsignals.wallets.sources.composite import CompositeWalletSource
from hlsignals.wallets.sources.curated import CuratedSource
from hlsignals.wallets.sources.paid import ApifySource, NansenSource
from tests.factories import AS_OF

Entry = tuple[str, datetime]  # (address as written by the source, as_of)
Builder = Callable[[Path, Sequence[Entry]], WalletSourcePort]


def write_curated(tmp: Path, body: str) -> Path:
    path = tmp / "wallets.toml"
    path.write_text(body)
    return path


def curated(tmp: Path, entries: Sequence[Entry]) -> WalletSourcePort:
    body = "".join(f'[[wallets]]\naddress = "{a}"\nas_of = {t.isoformat()}\n\n' for a, t in entries)
    return CuratedSource(write_curated(tmp, body), FakeClock(AS_OF))


def census(_: Path, entries: Sequence[Entry]) -> WalletSourcePort:
    registry = InMemoryRegistry()
    for address, as_of in entries:
        registry.observe([address], to_ms(as_of))
    return CensusSource(registry, min_observations=1)


def nansen(_: Path, entries: Sequence[Entry]) -> WalletSourcePort:
    wallets = [RawWallet(a, t) for a, t in entries]
    return NansenSource({"NANSEN_API_KEY": "k"}, fetch=lambda key: wallets)


def apify(_: Path, entries: Sequence[Entry]) -> WalletSourcePort:
    wallets = [RawWallet(a, t) for a, t in entries]
    return ApifySource({"APIFY_API_TOKEN": "k"}, fetch=lambda key: wallets)


def composite(tmp: Path, entries: Sequence[Entry]) -> WalletSourcePort:
    return CompositeWalletSource([curated(tmp, entries)])


BUILDERS: dict[str, Builder] = {
    "curated": curated,
    "census": census,
    "nansen": nansen,
    "apify": apify,
    "composite": composite,
}

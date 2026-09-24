"""Every WalletRegistry implementation honours the same contract."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from hlsignals.wallets.census.registry import (
    InMemoryRegistry,
    SqliteRegistry,
    WalletObservation,
    WalletRegistry,
)
from tests.factories import OTHER_WALLET, WALLET

BUILDERS: dict[str, Callable[[Path], WalletRegistry]] = {
    "memory": lambda _: InMemoryRegistry(),
    "sqlite_memory": lambda _: SqliteRegistry(":memory:"),
    "sqlite_file": lambda tmp: SqliteRegistry(tmp / "census.db"),
}


@pytest.fixture(params=sorted(BUILDERS))
def registry(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[WalletRegistry]:
    reg = BUILDERS[request.param](tmp_path)
    yield reg
    reg.close()


def test_unknown_wallet(registry: WalletRegistry) -> None:
    assert registry.get(WALLET) is None
    assert registry.observations(min_fills=0) == []


def test_first_observation(registry: WalletRegistry) -> None:
    registry.observe([WALLET, OTHER_WALLET], 1_000)
    assert registry.get(WALLET) == WalletObservation(WALLET, 1_000, 1_000, 1)


def test_last_seen_updates_and_counts(registry: WalletRegistry) -> None:
    registry.observe([WALLET], 1_000)
    registry.observe([WALLET], 3_000)
    registry.observe([WALLET], 2_000)  # out of order: first/last are min/max
    assert registry.get(WALLET) == WalletObservation(WALLET, 1_000, 3_000, 3)


def test_same_address_twice_in_one_trade_counts_once(registry: WalletRegistry) -> None:
    registry.observe([WALLET, WALLET], 1_000)  # self-trade
    observation = registry.get(WALLET)
    assert observation is not None
    assert observation.n_fills == 1


def test_observations_threshold_inclusive_and_sorted(registry: WalletRegistry) -> None:
    registry.observe([OTHER_WALLET, WALLET], 1)
    registry.observe([OTHER_WALLET], 2)
    assert [o.address for o in registry.observations(min_fills=1)] == sorted([WALLET, OTHER_WALLET])
    assert [o.address for o in registry.observations(min_fills=2)] == [OTHER_WALLET]
    assert registry.observations(min_fills=3) == []


def test_sqlite_round_trip_persists(tmp_path: Path) -> None:
    path = tmp_path / "census.db"
    first = SqliteRegistry(path)
    first.observe([WALLET], 5)
    first.close()
    reopened = SqliteRegistry(path)
    assert reopened.get(WALLET) == WalletObservation(WALLET, 5, 5, 1)
    reopened.close()

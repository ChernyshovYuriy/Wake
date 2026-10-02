from __future__ import annotations

import tomllib
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from hlsignals.app.config import DiscoverySettings
from hlsignals.app.discover import DiscoveryStore, discover, select_candidates, write_shortlist
from hlsignals.app.vet import VetResult
from hlsignals.core.clock import MS_PER_DAY, to_ms
from hlsignals.domain.models import WalletRecord
from hlsignals.wallets.census.registry import InMemoryRegistry, WalletObservation
from tests.factories import AS_OF, make_scored_wallet, make_wallet_record, wallet_address

NOW_MS = to_ms(AS_OF)
A, B, C, D = (wallet_address(i) for i in (1, 2, 3, 4))


def obs(address: str, n: int) -> WalletObservation:
    return WalletObservation(address, NOW_MS, NOW_MS, n)  # n fills in one instant: n per day


def accepted(address: str, trust: float = 0.5) -> VetResult:
    return VetResult(
        make_wallet_record(address=address),
        make_scored_wallet(address=address, trust=trust),
        None,
        None,
    )


def rejected(address: str) -> VetResult:
    return VetResult(
        make_wallet_record(address=address), None, ("maker_profile", "prescreen: ..."), None
    )


def errored(address: str) -> VetResult:
    return VetResult(make_wallet_record(address=address), None, None, "RetryableError: down")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[DiscoveryStore]:
    s = DiscoveryStore(tmp_path / "d.sqlite")
    yield s
    s.close()


MIN_TRUST = 0.4
MAX_FILLS_PER_DAY = 200.0


def test_store_round_trip_and_update(store: DiscoveryStore) -> None:
    store.record(accepted(A, 0.61), NOW_MS)
    store.record(rejected(B), NOW_MS)
    assert store.last(A) == (NOW_MS, True)
    assert store.last(B) == (NOW_MS, False)
    assert store.last(C) is None
    store.record(rejected(A), NOW_MS + 1)  # an accepted wallet that degrades drops out
    assert store.shortlist(MIN_TRUST) == []


def test_errors_are_not_recorded_so_they_are_retried(store: DiscoveryStore) -> None:
    store.record(errored(A), NOW_MS)
    assert store.last(A) is None


def test_candidates_order_and_limits(store: DiscoveryStore) -> None:
    store.record(accepted(A), NOW_MS - 1 * MS_PER_DAY)  # accepted: always re-vetted
    store.record(rejected(B), NOW_MS - 1 * MS_PER_DAY)  # rejected recently: skipped
    store.record(rejected(C), NOW_MS - 30 * MS_PER_DAY)  # rejected long ago: due again
    new_busy, new_quiet = wallet_address(10), wallet_address(11)
    observations = [
        obs(A, 50),
        obs(B, 90),
        obs(C, 40),
        obs(new_quiet, 21),
        obs(new_busy, 80),
        obs(D, 5),
    ]
    settings = DiscoverySettings(min_observations=20, max_wallets_per_run=10, revet_days=28.0)
    picked = select_candidates(observations, store, NOW_MS, settings, MAX_FILLS_PER_DAY)
    # accepted re-checks first, then never vetted (least active first), then due re-vets;
    # B (recent reject) and D (too few observations) are left out.
    assert [r.address for r in picked.records] == [A, new_quiet, new_busy, C]
    assert (picked.recent, picked.prescreened, picked.deferred) == (1, 0, 0)
    assert picked.records[2].source == "census"
    assert picked.records[2].raw_metric == "census_fills=80, census_fills_per_day=80.0"
    capped = select_candidates(
        observations, store, NOW_MS, DiscoverySettings(max_wallets_per_run=2), MAX_FILLS_PER_DAY
    )
    assert [r.address for r in capped.records] == [A, new_quiet]  # re-checks never starve
    assert capped.deferred == 2


def test_census_rate_over_the_maker_limit_skips_vetting(store: DiscoveryStore) -> None:
    store.record(accepted(A), NOW_MS - 1 * MS_PER_DAY)
    store.record(rejected(C), NOW_MS - 30 * MS_PER_DAY)
    one_day = NOW_MS - NOW_MS % MS_PER_DAY  # all observations inside today's UTC day
    maker, swing = wallet_address(10), wallet_address(11)
    observations = [
        WalletObservation(a, one_day, NOW_MS, n)
        for a, n in ((A, 500), (C, 500), (maker, 201), (swing, 200))
    ]
    picked = select_candidates(observations, store, NOW_MS, DiscoverySettings(), MAX_FILLS_PER_DAY)
    # A is accepted: re-vetted in full whatever its census rate; C and the maker are
    # prescreened; the swing wallet sits exactly on the limit, which passes.
    assert [r.address for r in picked.records] == [A, swing]
    assert picked.prescreened == 2
    spread = [WalletObservation(maker, one_day - 2 * MS_PER_DAY, NOW_MS, 201)]  # 3 days
    assert (
        select_candidates(spread, store, NOW_MS, DiscoverySettings(), MAX_FILLS_PER_DAY).prescreened
        == 0
    )


def test_shortlist_file_is_a_curated_wallets_file(store: DiscoveryStore, tmp_path: Path) -> None:
    store.record(accepted(A, 0.42), NOW_MS)
    store.record(accepted(B, 0.71), NOW_MS)
    store.record(accepted(C, 0.39), NOW_MS)  # accepted, but below the signals' min_trust
    path = tmp_path / "out" / "shortlist.toml"
    write_shortlist(path, store.shortlist(MIN_TRUST), AS_OF)
    doc = tomllib.loads(path.read_text())
    assert [w["address"] for w in doc["wallets"]] == [B, A]  # by trust; C cannot count yet
    assert "trust 0.710" in doc["wallets"][0]["note"]
    assert "score" not in doc["wallets"][0]  # trust is not an external prior score
    assert [e.address for e in store.shortlist(0.39)] == [B, A, C]  # the bar is inclusive


class FakePipeline:
    def __init__(self, results: dict[str, VetResult]) -> None:
        self.results = results
        self.vetted: list[WalletRecord] = []

    def vet(self, records: Sequence[WalletRecord], as_of: datetime) -> list[VetResult]:
        self.vetted = list(records)
        return [self.results[r.address] for r in records]


def test_discover_end_to_end(store: DiscoveryStore, tmp_path: Path) -> None:
    registry = InMemoryRegistry()
    for address, n in ((A, 30), (B, 25), (C, 3)):
        for i in range(n):
            registry.observe([address], NOW_MS - i)
    pipeline = FakePipeline({A: accepted(A, 0.6), B: rejected(B)})
    shortlist = tmp_path / "shortlist.toml"
    settings = DiscoverySettings(min_observations=20)
    summary = discover(
        pipeline=pipeline,
        registry=registry,
        store=store,
        settings=settings,
        max_fills_per_day=MAX_FILLS_PER_DAY,
        min_trust=MIN_TRUST,
        as_of=AS_OF,
        shortlist_path=shortlist,
    )
    assert [r.address for r in pipeline.vetted] == [B, A]  # least active first
    assert (summary.vetted, summary.accepted, summary.shortlisted) == (2, 1, 1)
    assert summary.rejected == {"maker_profile": 1}
    assert [w["address"] for w in tomllib.loads(shortlist.read_text())["wallets"]] == [A]
    again = discover(
        pipeline=pipeline,
        registry=registry,
        store=store,
        settings=settings,
        max_fills_per_day=MAX_FILLS_PER_DAY,
        min_trust=MIN_TRUST,
        as_of=AS_OF + timedelta(days=1),
        shortlist_path=shortlist,
    )
    assert [r.address for r in pipeline.vetted] == [A]  # B was rejected a day ago: skipped
    assert (again.skipped_recent, again.prescreened, again.deferred) == (1, 0, 0)


def test_store_reads_for_the_dashboard(store: DiscoveryStore) -> None:
    assert store.last_vetted_ms() is None
    store.record(accepted(A, 0.5), NOW_MS - 10)
    store.record(rejected(B), NOW_MS)
    store.record(
        VetResult(make_wallet_record(address=C), None, ("min_sample", "3 < 10"), None), NOW_MS - 5
    )
    assert [r.address for r in store.recent(2)] == [B, C]
    assert store.recent(10)[-1].trust == 0.5
    assert store.rejection_counts() == {"maker_profile": 1, "min_sample": 1}
    assert store.last_vetted_ms() == NOW_MS

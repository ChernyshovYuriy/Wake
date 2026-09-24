from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from hlsignals.core.clock import FakeClock, from_ms
from hlsignals.core.errors import ConfigError, RetryableError, SourceError
from hlsignals.wallets.census.registry import InMemoryRegistry, WalletObservation
from hlsignals.wallets.sources.base import (
    RawWallet,
    SourceDiagnostic,
    SourceResult,
    WalletSourcePort,
)
from hlsignals.wallets.sources.census import CensusSource
from hlsignals.wallets.sources.composite import CompositeWalletSource
from hlsignals.wallets.sources.curated import CuratedSource
from hlsignals.wallets.sources.factory import SourceDeps, SourceFactory, default_source_factory
from hlsignals.wallets.sources.paid import NansenSource
from tests.factories import AS_OF, OTHER_WALLET, WALLET, make_wallet_record
from tests.wallets.sources.support import write_curated

CLOCK = FakeClock(AS_OF)


# --- curated ------------------------------------------------------------------------


def test_curated_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="not found"):
        CuratedSource(tmp_path / "nope.toml", CLOCK).fetch()


def test_curated_malformed_toml(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="TOML"):
        CuratedSource(write_curated(tmp_path, "[[wallets]\n"), CLOCK).fetch()


def test_curated_wallets_must_be_a_list(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="list"):
        CuratedSource(write_curated(tmp_path, 'wallets = "x"\n'), CLOCK).fetch()


def test_curated_empty_file_has_no_wallets(tmp_path: Path) -> None:
    assert CuratedSource(write_curated(tmp_path, ""), CLOCK).fetch().records == ()


def test_curated_optional_score_metric_and_default_as_of(tmp_path: Path) -> None:
    body = (
        f'[[wallets]]\naddress = "{WALLET}"\nscore = 87.5\nmetric = "hyperdash_copy_score"\n'
        f'note = "seen on Hyperdash"\n\n[[wallets]]\naddress = "{OTHER_WALLET}"\n'
    )
    first, second = CuratedSource(write_curated(tmp_path, body), CLOCK).fetch().records
    assert (first.raw_score, first.raw_metric, first.as_of) == (87.5, "hyperdash_copy_score", AS_OF)
    assert (second.raw_score, second.raw_metric) == (None, None)


@pytest.mark.parametrize(
    ("entry", "match"),
    [
        ("score = -1.0", "raw_score"),
        ('score = "high"', "score"),
        ("scroe = 1.0", "unknown key"),
        ("as_of = 2026-09-24T00:00:00", "naive"),
        ("as_of = 2026-09-24", "as_of must be a datetime"),
    ],
)
def test_curated_bad_entries_become_diagnostics(tmp_path: Path, entry: str, match: str) -> None:
    body = f'[[wallets]]\naddress = "{WALLET}"\n{entry}\n'
    result = CuratedSource(write_curated(tmp_path, body), CLOCK).fetch()
    assert result.records == ()
    assert any(match in d.message for d in result.diagnostics)


def test_curated_entry_without_address(tmp_path: Path) -> None:
    result = CuratedSource(write_curated(tmp_path, "[[wallets]]\nscore = 1.0\n"), CLOCK).fetch()
    assert any("address" in d.message for d in result.diagnostics)


# --- paid (env-gated) ---------------------------------------------------------------


def test_paid_without_key_is_disabled_with_diagnostic() -> None:
    result = NansenSource({}, fetch=lambda key: []).fetch()
    assert result.records == ()
    assert "NANSEN_API_KEY" in result.diagnostics[0].message


def test_paid_receives_key() -> None:
    seen: list[str] = []

    def fetch(key: str) -> list[RawWallet]:
        seen.append(key)
        return []

    NansenSource({"NANSEN_API_KEY": "secret"}, fetch=fetch).fetch()
    assert seen == ["secret"]


def test_paid_http_failure_is_source_error() -> None:
    def fetch(key: str) -> list[RawWallet]:
        raise RetryableError("503", status=503)

    with pytest.raises(SourceError, match="nansen"):
        NansenSource({"NANSEN_API_KEY": "k"}, fetch=fetch).fetch()


def test_paid_without_integration_fails_loudly_instead_of_guessing() -> None:
    with pytest.raises(SourceError, match="not implemented"):
        NansenSource({"NANSEN_API_KEY": "k"}, fetch=None).fetch()


def test_paid_secret_not_in_repr() -> None:
    assert "secret" not in repr(NansenSource({"NANSEN_API_KEY": "secret"}, fetch=None))


def test_paid_rejects_non_raw_wallet_items() -> None:
    def fetch(key: str) -> list[Any]:
        return [{"address": WALLET}]  # a vendor adapter returning the wrong type

    result = NansenSource({"NANSEN_API_KEY": "k"}, fetch=fetch).fetch()
    assert result.records == ()
    assert result.diagnostics


# --- census -------------------------------------------------------------------------


def test_census_cold_start_is_empty() -> None:
    assert CensusSource(InMemoryRegistry(), min_observations=1).fetch().records == ()


def test_census_min_observations_threshold_inclusive() -> None:
    registry = InMemoryRegistry()
    for t in (1, 2, 3):
        registry.observe([WALLET], t)
    registry.observe([OTHER_WALLET], 1)
    (record,) = CensusSource(registry, min_observations=3).fetch().records
    assert record.address == WALLET
    assert record.raw_metric == "census_fills=3"
    assert record.as_of == from_ms(3)


def test_census_registry_failure_is_source_error() -> None:
    class Broken(InMemoryRegistry):
        def observations(self, min_fills: int) -> list[WalletObservation]:
            raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(SourceError, match="disk"):
        CensusSource(Broken(), min_observations=1).fetch()


def test_census_min_observations_validated() -> None:
    with pytest.raises(ConfigError, match="min_observations"):
        CensusSource(InMemoryRegistry(), min_observations=0)


# --- composite ----------------------------------------------------------------------


class Fixed:
    def __init__(self, name: str, result: SourceResult | Exception) -> None:
        self.name = name
        self.result = result

    def fetch(self) -> SourceResult:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def fixed(name: str, *addresses: str, score: float | None = None) -> Fixed:
    records = tuple(make_wallet_record(address=a, source=name, raw_score=score) for a in addresses)
    return Fixed(name, SourceResult(records, ()))


def test_composite_precedence_on_conflict() -> None:
    low, high = fixed("census", WALLET), fixed("curated", WALLET, score=90.0)
    result = CompositeWalletSource([low, high], precedence=["curated", "census"]).fetch()
    (record,) = result.records
    assert record.source == "curated"
    assert record.raw_score == 90.0


def test_composite_default_precedence_is_source_order() -> None:
    result = CompositeWalletSource([fixed("a", WALLET), fixed("b", WALLET, OTHER_WALLET)]).fetch()
    assert [(r.address, r.source) for r in result.records] == [(WALLET, "a"), (OTHER_WALLET, "b")]


def test_composite_isolates_one_failing_source() -> None:
    broken = Fixed("nansen", SourceError("API down"))
    result = CompositeWalletSource([broken, fixed("curated", WALLET)]).fetch()
    assert [r.address for r in result.records] == [WALLET]
    assert (result.used, result.failed) == (("curated",), ("nansen",))
    assert any("nansen" in d.source and "API down" in d.message for d in result.diagnostics)


def test_composite_all_failing_is_source_error() -> None:
    sources: list[WalletSourcePort] = [Fixed("a", SourceError("x")), Fixed("b", SourceError("y"))]
    with pytest.raises(SourceError, match="all wallet sources failed"):
        CompositeWalletSource(sources).fetch()


def test_composite_keeps_child_diagnostics() -> None:
    child = Fixed("curated", SourceResult((), (SourceDiagnostic("curated", "bad row"),)))
    assert CompositeWalletSource([child]).fetch().diagnostics[0].message == "bad row"


@pytest.mark.parametrize(
    ("sources", "precedence", "match"),
    [
        ([], None, "at least one"),
        ([fixed("a"), fixed("a")], None, "duplicate"),
        ([fixed("a")], ["b"], "unknown source"),
    ],
)
def test_composite_config_errors(
    sources: list[WalletSourcePort], precedence: list[str] | None, match: str
) -> None:
    with pytest.raises(ConfigError, match=match):
        CompositeWalletSource(sources, precedence=precedence)


# --- factory ------------------------------------------------------------------------


def deps(tmp_path: Path) -> SourceDeps:
    return SourceDeps(clock=CLOCK, env={}, registry=InMemoryRegistry(), base_dir=tmp_path)


@pytest.mark.parametrize(
    ("spec", "kind"),
    [
        ({"type": "curated", "path": "wallets.toml"}, CuratedSource),
        ({"type": "census", "min_observations": 20}, CensusSource),
        ({"type": "nansen"}, NansenSource),
    ],
)
def test_factory_builds_each_type(tmp_path: Path, spec: dict[str, Any], kind: type) -> None:
    assert isinstance(default_source_factory().create(spec, deps(tmp_path)), kind)


def test_factory_resolves_relative_paths_against_base_dir(tmp_path: Path) -> None:
    write_curated(tmp_path, f'[[wallets]]\naddress = "{WALLET}"\n')
    source = default_source_factory().create(
        {"type": "curated", "path": "wallets.toml"}, deps(tmp_path)
    )
    assert source.fetch().records[0].address == WALLET


def test_factory_unknown_type_lists_valid_types(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"apify.*census.*curated.*nansen"):
        default_source_factory().create({"type": "leaderboard"}, deps(tmp_path))


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ({}, "type"),
        ({"type": "curated"}, "path"),
        ({"type": "census", "min_observations": "x"}, "min_observations"),
    ],
)
def test_factory_bad_spec(tmp_path: Path, spec: dict[str, Any], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        default_source_factory().create(spec, deps(tmp_path))


def test_factory_register_rejects_duplicate() -> None:
    factory = SourceFactory({})
    factory.register("x", lambda spec, d: fixed("x"))
    with pytest.raises(ConfigError, match="already registered"):
        factory.register("x", lambda spec, d: fixed("x"))


def test_composite_of_factory_sources_end_to_end(tmp_path: Path) -> None:
    write_curated(tmp_path, f'[[wallets]]\naddress = "{WALLET}"\nscore = 50.0\n')
    d = deps(tmp_path)
    d.registry.observe([WALLET, OTHER_WALLET], 1)
    factory = default_source_factory()
    sources = [
        factory.create({"type": "curated", "path": "wallets.toml"}, d),
        factory.create({"type": "census", "min_observations": 1}, d),
        factory.create({"type": "nansen"}, d),  # no key: disabled, not failed
    ]
    result = CompositeWalletSource(sources, precedence=["curated", "census", "nansen"]).fetch()
    assert {(r.address, r.source) for r in result.records} == {
        (WALLET, "curated"),
        (OTHER_WALLET, "census"),
    }
    assert any("disabled" in diag.message for diag in result.diagnostics)

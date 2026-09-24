"""Every WalletSource honours the same contract; add a builder to cover a new one."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from hlsignals.domain.models import WalletRecord
from hlsignals.wallets.sources.base import SourceResult
from tests.factories import AS_OF, OTHER_WALLET, WALLET
from tests.wallets.sources.support import BUILDERS, Builder

EARLIER = AS_OF - timedelta(days=3)


@pytest.fixture(params=sorted(BUILDERS))
def build(request: pytest.FixtureRequest) -> Builder:
    return BUILDERS[request.param]


def test_returns_records_tagged_with_source_name(build: Builder, tmp_path: Path) -> None:
    source = build(tmp_path, [(WALLET, AS_OF), (OTHER_WALLET, AS_OF)])
    result = source.fetch()
    assert isinstance(result, SourceResult)
    assert {r.address for r in result.records} == {WALLET, OTHER_WALLET}
    assert all(isinstance(r, WalletRecord) for r in result.records)
    assert {r.source for r in result.records} <= {source.name, "curated"}


def test_empty_is_ok(build: Builder, tmp_path: Path) -> None:
    assert build(tmp_path, []).fetch().records == ()


def test_invalid_address_rejected_with_diagnostic(build: Builder, tmp_path: Path) -> None:
    result = build(tmp_path, [("0xnothex", AS_OF), (WALLET, AS_OF)]).fetch()
    assert [r.address for r in result.records] == [WALLET]
    assert any("0xnothex" in d.message for d in result.diagnostics)


def test_addresses_lower_cased_and_duplicates_keep_newest(build: Builder, tmp_path: Path) -> None:
    upper = "0x" + WALLET[2:].upper()
    result = build(tmp_path, [(upper, EARLIER), (WALLET, AS_OF), (upper, EARLIER)]).fetch()
    (record,) = result.records
    assert record.address == WALLET
    assert record.as_of == AS_OF


def test_reports_itself_as_used(build: Builder, tmp_path: Path) -> None:
    result = build(tmp_path, [(WALLET, AS_OF)]).fetch()
    assert result.used
    assert result.failed == ()

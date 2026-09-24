"""Guards the Phase 0 golden fixtures and the facts docs/api-notes.md derives from them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FIXTURE_DIR, load_fixture

FIXTURE_NAMES = sorted(p.stem for p in FIXTURE_DIR.glob("*.json"))
PERP_BUY_DIRS = {"Open Long", "Close Short", "Short > Long"}
PERP_SELL_DIRS = {"Close Long", "Open Short", "Long > Short"}
REQUIRED_FILL_KEYS = {
    "coin",
    "px",
    "sz",
    "side",
    "time",
    "startPosition",
    "dir",
    "closedPnl",
    "hash",
    "oid",
    "crossed",
    "fee",
    "feeToken",
    "tid",
    "twapId",
}
PAGE_CAP = 2000


def all_fills() -> list[dict[str, Any]]:
    fills: list[dict[str, Any]] = []
    for name in FIXTURE_NAMES:
        if name.startswith("fills_"):
            fills.extend(load_fixture(name)["response"])
    fills.extend(load_fixture("dir_catalog")["response"].values())
    return fills


def test_fixtures_exist() -> None:
    assert len(FIXTURE_NAMES) >= 15


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_shape(name: str) -> None:
    doc = json.loads((FIXTURE_DIR / f"{name}.json").read_text())
    assert set(doc) <= {"request", "response", "status"}
    assert {"request", "response"} <= set(doc)


def test_fills_have_required_keys() -> None:
    for fill in all_fills():
        assert set(fill) >= REQUIRED_FILL_KEYS, fill


def test_perp_dir_sign_matches_side() -> None:
    for fill in all_fills():
        if fill["dir"] in PERP_BUY_DIRS | PERP_SELL_DIRS:
            assert (fill["dir"] in PERP_BUY_DIRS) == (fill["side"] == "B"), fill


def test_heavy_pages_hit_cap_and_overlap_at_inclusive_boundary() -> None:
    page1 = load_fixture("fills_heavy_page1")["response"]
    page2 = load_fixture("fills_heavy_page2")["response"]
    assert len(page1) == PAGE_CAP
    assert [f["time"] for f in page1] == sorted(f["time"] for f in page1)
    assert page2[0]["time"] == page1[-1]["time"]
    keys1 = {(f["tid"], f["hash"]) for f in page1}
    assert any((f["tid"], f["hash"]) in keys1 for f in page2)


def test_meta_and_ctxs_align_and_prefix_names() -> None:
    meta, ctxs = load_fixture("meta_and_ctxs_xyz")["response"]
    assert len(meta["universe"]) == len(ctxs)
    assert all(u["name"].startswith("xyz:") for u in meta["universe"])


def test_error_fixtures_record_status() -> None:
    assert load_fixture("error_candles_bare_coin")["status"] == 500
    assert load_fixture("error_unknown_type")["status"] == 422


def test_no_real_address_leaked(fixture_dir: Path) -> None:
    # Every address in a request must be a pseudonym also used in responses; the one
    # real address used during probing must never appear.
    for path in fixture_dir.glob("*.json"):
        assert "0x82fd11271061ad9b2e6b856e2beb3ad1e4d7f316" not in path.read_text()

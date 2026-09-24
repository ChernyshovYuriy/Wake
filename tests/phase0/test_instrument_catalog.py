"""The instrument catalog must classify every live HIP-3 market in the captured fixtures."""

from __future__ import annotations

import tomllib
from collections import Counter
from pathlib import Path

from tests.conftest import FIXTURE_DIR, load_fixture

CATALOG = Path(__file__).parents[2] / "config" / "instruments.toml"
EXPECTED_CLASSES = {
    "equity_us",
    "equity_foreign",
    "etf",
    "index",
    "commodity",
    "fx",
    "rates",
    "pre_ipo",
    "crypto",
    "unverified",
}


def catalog() -> dict[str, list[str]]:
    classes: dict[str, list[str]] = tomllib.loads(CATALOG.read_text())["classes"]
    return classes


def live_symbols() -> set[str]:
    live: set[str] = set()
    for path in FIXTURE_DIR.glob("meta_and_ctxs_*.json"):
        meta, _ = load_fixture(path.stem)["response"]
        live |= {u["name"] for u in meta["universe"] if not u.get("isDelisted")}
    return live


def test_catalog_has_exactly_the_known_classes() -> None:
    assert set(catalog()) == EXPECTED_CLASSES


def test_every_symbol_classified_once() -> None:
    counts = Counter(s for symbols in catalog().values() for s in symbols)
    assert [s for s, n in counts.items() if n > 1] == []


def test_every_live_fixture_market_is_classified() -> None:
    classified = {s for symbols in catalog().values() for s in symbols}
    assert sorted(live_symbols() - classified) == []


def test_catalog_symbols_are_hip3() -> None:
    for symbols in catalog().values():
        for symbol in symbols:
            dex, _, coin = symbol.partition(":")
            assert dex
            assert coin
            assert dex == dex.lower()


def test_catalog_lists_no_unknown_symbols() -> None:
    # Catches typos. After a recapture, delisted markets must be removed from the catalog.
    classified = {s for symbols in catalog().values() for s in symbols}
    assert sorted(classified - live_symbols()) == []

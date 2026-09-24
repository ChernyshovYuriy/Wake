from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hlsignals.core.errors import ConfigError
from hlsignals.domain.symbols import Symbol
from hlsignals.universe.instruments import InstrumentCatalog

CATALOG_FILE = Path(__file__).parents[2] / "config" / "instruments.toml"


def test_real_catalog_loads() -> None:
    catalog = InstrumentCatalog.from_mapping(tomllib.loads(CATALOG_FILE.read_text()))
    assert catalog.class_of(Symbol("xyz", "NVDA")) == "equity_us"
    assert catalog.class_of(Symbol("para", "TOTAL2")) == "crypto"
    assert catalog.class_of(Symbol("xyz", "NOPE")) is None
    assert "equity_us" in catalog.class_names


def test_symbols_in_classes() -> None:
    catalog = InstrumentCatalog.from_mapping(
        {"classes": {"equity_us": ["xyz:A", "para:B"], "etf": ["xyz:C"]}}
    )
    assert catalog.symbols_in(frozenset({"equity_us"})) == frozenset(
        {Symbol("xyz", "A"), Symbol("para", "B")}
    )


def test_check_classes() -> None:
    catalog = InstrumentCatalog.from_mapping({"classes": {"equity_us": ["xyz:A"]}})
    catalog.check_classes(frozenset({"equity_us"}))
    with pytest.raises(ConfigError, match="unknown instrument class"):
        catalog.check_classes(frozenset({"equities"}))


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ({}, "classes"),
        ({"classes": {"a": ["xyz:A"], "b": ["xyz:A"]}}, "more than one class"),
        ({"classes": {"a": ["xyz:A:B"]}}, "xyz:A:B"),
        ({"classes": {"a": ["BTC"]}}, "HIP-3"),
        ({"classes": {"a": "xyz:A"}}, "list"),
    ],
)
def test_invalid_catalog(raw: dict[str, object], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        InstrumentCatalog.from_mapping(raw)

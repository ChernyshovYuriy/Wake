from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hlsignals.core.errors import AdapterError, ConfigError, RetryableError
from hlsignals.domain.models import Dex, MarketCtx
from hlsignals.domain.symbols import Symbol
from hlsignals.infra.gateway import HyperliquidGateway
from hlsignals.infra.pagination import FillPaging
from hlsignals.infra.transport import FixtureTransport
from hlsignals.universe.discovery import EquityDexLocator, MarketDataPort
from hlsignals.universe.instruments import InstrumentCatalog
from tests.conftest import FIXTURE_DIR
from tests.factories import make_market_ctx

EQUITY = frozenset({"equity_us"})
CATALOG = InstrumentCatalog.from_mapping(
    {
        "classes": {
            "equity_us": ["xyz:NVDA", "xyz:AVGO", "para:AVGO", "para:SOFI", "io:IONQ"],
            "etf": ["xyz:SMH"],
            "crypto": ["para:TOTAL2"],
        }
    }
)


def ctx(raw: str, **overrides: object) -> MarketCtx:
    return make_market_ctx(symbol=Symbol.parse(raw), **overrides)


class FakePort:
    def __init__(self, markets: dict[str, list[MarketCtx] | Exception]) -> None:
        self.markets = markets
        self.queried: list[str] = []

    def perp_dexs(self) -> list[Dex]:
        return [Dex(name, name.upper()) for name in self.markets]

    def meta_and_ctxs(self, dex: str) -> list[MarketCtx]:
        self.queried.append(dex)
        result = self.markets[dex]
        if isinstance(result, Exception):
            raise result
        return result


def locator(
    port: MarketDataPort, preference: tuple[str, ...] = ("xyz", "para", "io")
) -> EquityDexLocator:
    return EquityDexLocator(port, CATALOG, EQUITY, preference)


def test_no_builder_dexes() -> None:
    result = locator(FakePort({})).locate()
    assert result.markets == ()
    assert result.dexes_queried == ()


def test_dex_without_included_instruments_is_skipped_and_reported() -> None:
    port = FakePort({"xyz": [ctx("xyz:NVDA")], "km": [ctx("km:US500")]})
    result = locator(port).locate()
    assert port.queried == ["xyz"]
    assert result.dexes_skipped == ("km",)


def test_preference_orders_dexes_and_shadows_duplicate_coins() -> None:
    port = FakePort({"para": [ctx("para:AVGO"), ctx("para:SOFI")], "xyz": [ctx("xyz:AVGO")]})
    result = locator(port, preference=("xyz", "para")).locate()
    assert port.queried == ["xyz", "para"]
    assert [str(m.symbol) for m in result.markets] == ["xyz:AVGO", "para:SOFI"]
    assert result.shadowed == {Symbol("para", "AVGO"): Symbol("xyz", "AVGO")}


def test_unlisted_preference_dexes_come_after_in_name_order() -> None:
    port = FakePort({"para": [ctx("para:SOFI")], "io": [ctx("io:IONQ")], "xyz": [ctx("xyz:NVDA")]})
    locator(port, preference=("xyz",)).locate()
    assert port.queried == ["xyz", "io", "para"]


@pytest.mark.parametrize("error", [RetryableError("down", status=503), AdapterError("bad shape")])
def test_one_failing_dex_does_not_hide_others(error: Exception) -> None:
    port = FakePort({"xyz": error, "para": [ctx("para:SOFI")]})
    result = locator(port).locate()
    assert [str(m.symbol) for m in result.markets] == ["para:SOFI"]
    assert "xyz" in result.dex_failures
    assert str(error) in result.dex_failures["xyz"]


def test_classes_filter_unclassified_and_delisted() -> None:
    port = FakePort(
        {
            "xyz": [
                ctx("xyz:NVDA"),
                ctx("xyz:SMH"),  # etf: excluded by class
                ctx("xyz:NEWCO"),  # live but not in catalog
                ctx("xyz:OLDCO", is_delisted=True),  # delisted and unknown: ignored silently
            ],
            "para": [ctx("para:TOTAL2")],
        }
    )
    result = locator(port).locate()
    assert [str(m.symbol) for m in result.markets] == ["xyz:NVDA"]
    assert result.excluded_by_class == {"etf": 1, "crypto": 1}
    assert result.unclassified == (Symbol("xyz", "NEWCO"),)


def test_delisted_included_market_is_kept_for_the_filter_chain_to_explain() -> None:
    port = FakePort({"xyz": [ctx("xyz:NVDA", is_delisted=True)]})
    assert len(locator(port).locate().markets) == 1


def test_unknown_include_class_is_config_error() -> None:
    with pytest.raises(ConfigError):
        EquityDexLocator(FakePort({}), CATALOG, frozenset({"stocks"}), ())


def test_real_fixtures_with_real_catalog() -> None:
    catalog = InstrumentCatalog.from_mapping(
        tomllib.loads((Path(__file__).parents[2] / "config" / "instruments.toml").read_text())
    )
    gateway = HyperliquidGateway(FixtureTransport(FIXTURE_DIR), FillPaging(2000, 10_000))
    result = EquityDexLocator(gateway, catalog, EQUITY, ("xyz", "para", "io", "mkts")).locate()
    symbols = {m.symbol for m in result.markets}
    assert Symbol("xyz", "NVDA") in symbols
    assert result.shadowed[Symbol("para", "AVGO")] == Symbol("xyz", "AVGO")
    assert result.unclassified == ()
    assert result.dex_failures == {}
    assert all(catalog.class_of(s) == "equity_us" for s in symbols)
    assert "mkts" not in result.dexes_queried  # no US stocks listed there

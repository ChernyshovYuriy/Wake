from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import pytest

from hlsignals.core.errors import AdapterError
from hlsignals.domain.models import Side
from hlsignals.domain.symbols import Symbol
from hlsignals.infra.adapters import (
    adapt_candles,
    adapt_fills,
    adapt_l2_book,
    adapt_markets,
    adapt_perp_dexs,
    adapt_positions,
)
from tests.conftest import load_fixture


def response(name: str) -> Any:
    return copy.deepcopy(load_fixture(name)["response"])


def request(name: str) -> Any:
    return load_fixture(name)["request"]


# --- perpDexs -----------------------------------------------------------------------


def test_perp_dexs_skips_core_placeholder() -> None:
    dexes = adapt_perp_dexs(response("perp_dexs"))
    assert dexes[0].name == "xyz"
    assert dexes[0].full_name == "XYZ"
    assert {"xyz", "para", "io", "mkts"} <= {d.name for d in dexes}


@pytest.mark.parametrize("raw", [{"not": "a list"}, [None, {"fullName": "x"}], [None, 3]])
def test_perp_dexs_rejects_bad_shapes(raw: Any) -> None:
    with pytest.raises(AdapterError):
        adapt_perp_dexs(raw)


# --- metaAndAssetCtxs ---------------------------------------------------------------


@pytest.mark.parametrize("dex", ["xyz", "para", "io", "mkts"])
def test_markets_golden(dex: str) -> None:
    markets = adapt_markets(response(f"meta_and_ctxs_{dex}"), dex)
    assert markets
    assert all(m.symbol.dex == dex for m in markets)


def test_markets_parse_string_numerics_and_convert_oi() -> None:
    nvda = next(
        m for m in adapt_markets(response("meta_and_ctxs_xyz"), "xyz") if m.symbol.coin == "NVDA"
    )
    assert nvda.mark_px > 0
    assert nvda.oi_usd == pytest.approx(nvda.open_interest * nvda.mark_px)
    assert not nvda.is_delisted


def test_markets_delisted_flag_and_null_mid() -> None:
    delisted = [m for m in adapt_markets(response("meta_and_ctxs_xyz"), "xyz") if m.is_delisted]
    assert delisted
    assert any(m.mid_px is None for m in delisted)


def test_markets_length_mismatch() -> None:
    meta, ctxs = response("meta_and_ctxs_xyz")
    with pytest.raises(AdapterError, match="length"):
        adapt_markets([meta, ctxs[:-1]], "xyz")


def test_markets_missing_required_field() -> None:
    meta, ctxs = response("meta_and_ctxs_xyz")
    del ctxs[0]["markPx"]
    with pytest.raises(AdapterError, match="markPx"):
        adapt_markets([meta, ctxs], "xyz")


def test_markets_bad_numeric() -> None:
    meta, ctxs = response("meta_and_ctxs_xyz")
    ctxs[0]["openInterest"] = "lots"
    with pytest.raises(AdapterError, match="openInterest"):
        adapt_markets([meta, ctxs], "xyz")


def test_markets_symbol_from_other_dex_rejected() -> None:
    with pytest.raises(AdapterError, match="dex"):
        adapt_markets(response("meta_and_ctxs_xyz"), "para")


def test_markets_domain_violation_becomes_adapter_error() -> None:
    meta, ctxs = response("meta_and_ctxs_xyz")
    ctxs[0]["markPx"] = "0"
    with pytest.raises(AdapterError, match="xyz:"):
        adapt_markets([meta, ctxs], "xyz")


@pytest.mark.parametrize("raw", [[], [{}], {"a": 1}])
def test_markets_rejects_bad_top_level(raw: Any) -> None:
    with pytest.raises(AdapterError):
        adapt_markets(raw, "xyz")


# --- clearinghouseState -------------------------------------------------------------


def test_positions_golden() -> None:
    req = request("clearinghouse_positioned_xyz")
    positions = adapt_positions(response("clearinghouse_positioned_xyz"), req["user"])
    assert len(positions) > 10
    tsla = next(p for p in positions if p.symbol == Symbol("xyz", "TSLA"))
    assert tsla.size == Decimal("2.378")
    assert tsla.wallet == req["user"]
    assert any(p.size < 0 for p in positions)


def test_positions_empty() -> None:
    req = request("clearinghouse_active_core")
    assert adapt_positions(response("clearinghouse_active_core"), req["user"]) == []


def test_positions_missing_field() -> None:
    raw = response("clearinghouse_positioned_xyz")
    del raw["assetPositions"][0]["position"]["szi"]
    with pytest.raises(AdapterError, match="szi"):
        adapt_positions(raw, request("clearinghouse_positioned_xyz")["user"])


def test_positions_normalizes_wallet() -> None:
    req = request("clearinghouse_positioned_xyz")
    upper = "0x" + req["user"][2:].upper()
    positions = adapt_positions(response("clearinghouse_positioned_xyz"), upper)
    assert positions[0].wallet == req["user"]


# --- userFillsByTime ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["fills_active", "fills_heavy_page1", "fills_heavy_page2"])
def test_fills_golden(name: str) -> None:
    raw, user = response(name), request(name)["user"]
    fills = adapt_fills(raw, user)
    assert len(fills) == len(raw)
    assert {f.wallet for f in fills} == {user}


def test_fill_fields() -> None:
    raw = response("fills_active")[0]
    (fill,) = adapt_fills([raw], request("fills_active")["user"])
    assert fill.px == Decimal(raw["px"])
    assert fill.start_position == Decimal(raw["startPosition"])
    assert fill.side is Side(raw["side"])
    assert fill.dir == raw["dir"]
    assert fill.crossed is raw["crossed"]
    assert fill.tid == raw["tid"]
    assert fill.symbol == Symbol.parse(raw["coin"])


def test_fill_optional_fields_absent_and_liquidation_present() -> None:
    raw = response("fills_active")[0]
    for optional in ("cloid", "builderFee", "liquidation"):
        raw.pop(optional, None)
    wallet = request("fills_active")["user"]
    assert adapt_fills([raw], wallet)[0].liquidated_user is None
    raw["liquidation"] = {"liquidatedUser": "0x" + "AB" * 20, "markPx": "1", "method": "market"}
    assert adapt_fills([raw], wallet)[0].liquidated_user == "0x" + "ab" * 20


def test_non_perp_fills_are_kept_for_callers_to_drop() -> None:
    spot = {**response("fills_active")[0], "coin": "@142", "dir": "Buy", "side": "B"}
    (fill,) = adapt_fills([spot], request("fills_active")["user"])
    assert not fill.symbol.is_hip3


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda f: f.pop("px"), "px"),
        (lambda f: f.update(side="X"), "side"),
        (lambda f: f.update(time="soon"), "time"),
        (lambda f: f.update(crossed="yes"), "crossed"),
        (lambda f: f.update(coin="a:b:c"), "symbol"),
        (lambda f: f.update(sz="-1"), "sz"),
    ],
)
def test_fill_bad_shapes(mutate: Any, match: str) -> None:
    raw = response("fills_active")[0]
    mutate(raw)
    with pytest.raises(AdapterError, match=match):
        adapt_fills([raw], request("fills_active")["user"])


def test_fills_top_level_must_be_list() -> None:
    with pytest.raises(AdapterError):
        adapt_fills({"fills": []}, request("fills_active")["user"])


# --- candleSnapshot / l2Book --------------------------------------------------------


@pytest.mark.parametrize("name", ["candles_xyz_nvda_1h", "candles_xyz_nvda_1d"])
def test_candles_golden(name: str) -> None:
    candles = adapt_candles(response(name))
    assert candles
    assert all(c.symbol == Symbol("xyz", "NVDA") for c in candles)
    assert [c.open_ms for c in candles] == sorted(c.open_ms for c in candles)


def test_candles_bad_value() -> None:
    raw = response("candles_xyz_nvda_1h")
    raw[0]["h"] = "0"
    with pytest.raises(AdapterError):
        adapt_candles(raw)


def test_l2_book_golden() -> None:
    book = adapt_l2_book(response("l2_book_xyz_nvda"))
    assert book.symbol == Symbol("xyz", "NVDA")
    assert len(book.bids) == len(book.asks) == 20
    assert book.bids[0].px < book.asks[0].px


def test_l2_book_needs_two_sides() -> None:
    raw = response("l2_book_xyz_nvda")
    raw["levels"] = raw["levels"][:1]
    with pytest.raises(AdapterError, match="levels"):
        adapt_l2_book(raw)

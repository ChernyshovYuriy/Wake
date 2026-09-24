from __future__ import annotations

import itertools
from typing import Any

from hlsignals.core.clock import from_ms
from hlsignals.domain.symbols import Symbol
from hlsignals.infra.gateway import HyperliquidGateway
from hlsignals.infra.pagination import FillPaging
from hlsignals.infra.transport import FixtureTransport, Transport
from tests.conftest import FIXTURE_DIR, load_fixture

PAGING = FillPaging(page_cap=2000, history_cap=10_000)
NVDA = Symbol("xyz", "NVDA")


class Recording(Transport):
    def __init__(self, response: Any) -> None:
        self.response = response
        self.payloads: list[dict[str, Any]] = []

    def post(self, payload: Any) -> Any:
        self.payloads.append(dict(payload))
        return self.response


def fixture_gateway() -> HyperliquidGateway:
    return HyperliquidGateway(FixtureTransport(FIXTURE_DIR), PAGING)


def request(name: str) -> dict[str, Any]:
    req: dict[str, Any] = load_fixture(name)["request"]
    return req


# Payload shapes are proven by hitting the captured fixtures, whose requests are real.


def test_perp_dexs() -> None:
    assert "xyz" in {d.name for d in fixture_gateway().perp_dexs()}


def test_meta_and_ctxs() -> None:
    markets = fixture_gateway().meta_and_ctxs("xyz")
    assert NVDA in {m.symbol for m in markets}


def test_clearinghouse_state_with_dex() -> None:
    user = request("clearinghouse_positioned_xyz")["user"]
    assert fixture_gateway().clearinghouse_state(user, "xyz")


def test_clearinghouse_state_without_dex_omits_key() -> None:
    user = request("clearinghouse_active_core")["user"]
    assert fixture_gateway().clearinghouse_state(user, None) == []
    rec = Recording({"assetPositions": [], "time": 0})
    HyperliquidGateway(rec, PAGING).clearinghouse_state(user, None)
    assert rec.payloads == [{"type": "clearinghouseState", "user": user}]


def test_candle_snapshot() -> None:
    req = request("candles_xyz_nvda_1h")["req"]
    candles = fixture_gateway().candle_snapshot(
        NVDA, "1h", from_ms(req["startTime"]), from_ms(req["endTime"])
    )
    assert candles
    assert candles[0].symbol == NVDA


def test_l2_book() -> None:
    assert fixture_gateway().l2_book(NVDA).symbol == NVDA


def test_user_fills_single_page() -> None:
    req = request("fills_active")
    fills = list(
        fixture_gateway().user_fills_by_time(
            req["user"], from_ms(req["startTime"]), from_ms(req["endTime"])
        )
    )
    assert len(fills) == len(load_fixture("fills_active")["response"])


def test_user_fills_pages_through_real_captured_pages() -> None:
    req = request("fills_heavy_page1")
    it = fixture_gateway().user_fills_by_time(
        req["user"], from_ms(req["startTime"]), from_ms(req["endTime"])
    )
    # Page 2 is full too, so consuming past it would request page 3 (not captured).
    fills = list(itertools.islice(it, 3999))
    assert len({f.dedupe_key for f in fills}) == 3999  # boundary duplicate dropped
    assert it.pages_fetched == 2
    assert [f.time_ms for f in fills] == sorted(f.time_ms for f in fills)


def test_user_fills_without_end_omits_end_time() -> None:
    rec = Recording([])
    user = request("fills_active")["user"]
    list(HyperliquidGateway(rec, PAGING).user_fills_by_time(user, from_ms(5), None))
    assert rec.payloads == [{"type": "userFillsByTime", "user": user, "startTime": 5}]


def test_user_address_is_normalized_in_payload() -> None:
    rec = Recording({"assetPositions": [], "time": 0})
    HyperliquidGateway(rec, PAGING).clearinghouse_state("0x" + "AB" * 20, "xyz")
    assert rec.payloads[0]["user"] == "0x" + "ab" * 20

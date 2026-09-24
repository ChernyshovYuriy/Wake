"""Domain logic over real captured fills (via gateway + adapters + pagination)."""

from __future__ import annotations

import itertools

import pytest

from hlsignals.core.clock import from_ms
from hlsignals.domain.direction import is_perp_dir
from hlsignals.domain.lots import LotBook
from hlsignals.domain.models import Fill
from hlsignals.domain.symbols import Symbol
from hlsignals.infra.gateway import HyperliquidGateway
from hlsignals.infra.pagination import FillPaging
from hlsignals.infra.transport import FixtureTransport
from tests.conftest import FIXTURE_DIR, load_fixture

CAPTURED = {"fills_heavy_page1": 3999, "fills_active": None}  # heavy: two captured pages


def captured_fills(name: str) -> list[Fill]:
    req = load_fixture(name)["request"]
    gateway = HyperliquidGateway(FixtureTransport(FIXTURE_DIR), FillPaging(2000, 10_000))
    it = gateway.user_fills_by_time(req["user"], from_ms(req["startTime"]), from_ms(req["endTime"]))
    return list(itertools.islice(it, CAPTURED[name]))


@pytest.fixture(scope="module", params=sorted(CAPTURED))
def fills(request: pytest.FixtureRequest) -> list[Fill]:
    return captured_fills(request.param)


@pytest.fixture(scope="module")
def book(fills: list[Fill]) -> LotBook:
    book = LotBook()
    book.add_all(f for f in fills if is_perp_dir(f.dir))  # also checks side/dir agreement
    return book


def test_start_position_is_continuous_in_feed_order(book: LotBook) -> None:
    assert book.gaps == ()


def test_flat_to_flat_fifo_pnl_equals_api_closed_pnl(fills: list[Fill], book: LotBook) -> None:
    first: dict[Symbol, Fill] = {}
    for fill in fills:
        first.setdefault(fill.symbol, fill)
    round_trips = [
        s for s in book.symbols if first[s].start_position == 0 and book.position(s) == 0
    ]
    for symbol in round_trips:
        fifo = sum(lot.realized_pnl or 0 for lot in book.closed_lots if lot.symbol == symbol)
        assert fifo == book.api_closed_pnl(symbol)


def test_history_starting_mid_position_yields_orphans_not_guesses(book: LotBook) -> None:
    for lot in book.closed_lots:
        assert (lot.realized_pnl is None) == lot.is_orphan

"""HyperliquidGateway facade: one typed method per ``/info`` request type.

The single home of ``/info`` payload shapes; responses go through ``adapters``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from hlsignals.core.clock import to_ms
from hlsignals.domain.address import normalize_address
from hlsignals.domain.models import Candle, Dex, Fill, L2Book, MarketCtx, Position
from hlsignals.domain.symbols import Symbol
from hlsignals.infra.adapters import (
    adapt_candles,
    adapt_fills,
    adapt_l2_book,
    adapt_markets,
    adapt_perp_dexs,
    adapt_positions,
)
from hlsignals.infra.pagination import FillPaging, FillsIterator
from hlsignals.infra.transport import Transport


class HyperliquidGateway:
    def __init__(self, transport: Transport, paging: FillPaging) -> None:
        self._transport = transport
        self._paging = paging

    def perp_dexs(self) -> list[Dex]:
        return adapt_perp_dexs(self._transport.post({"type": "perpDexs"}))

    def meta_and_ctxs(self, dex: str) -> list[MarketCtx]:
        return adapt_markets(self._transport.post({"type": "metaAndAssetCtxs", "dex": dex}), dex)

    def clearinghouse_state(self, user: str, dex: str | None) -> list[Position]:
        """Open positions. HIP-3 positions are only returned when ``dex`` is given."""
        payload: dict[str, Any] = {"type": "clearinghouseState", "user": normalize_address(user)}
        if dex is not None:
            payload["dex"] = dex
        return adapt_positions(self._transport.post(payload), user)

    def user_fills_by_time(self, user: str, start: datetime, end: datetime | None) -> FillsIterator:
        wallet = normalize_address(user)

        def fetch_page(start_ms: int, end_ms: int | None) -> list[Fill]:
            payload: dict[str, Any] = {
                "type": "userFillsByTime",
                "user": wallet,
                "startTime": start_ms,
            }
            if end_ms is not None:
                payload["endTime"] = end_ms
            return adapt_fills(self._transport.post(payload), wallet)

        end_ms = None if end is None else to_ms(end)
        return FillsIterator(fetch_page, to_ms(start), end_ms, self._paging)

    def candle_snapshot(
        self, symbol: Symbol, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        req = {
            "coin": str(symbol),
            "interval": interval,
            "startTime": to_ms(start),
            "endTime": to_ms(end),
        }
        return adapt_candles(self._transport.post({"type": "candleSnapshot", "req": req}))

    def l2_book(self, symbol: Symbol) -> L2Book:
        return adapt_l2_book(self._transport.post({"type": "l2Book", "coin": str(symbol)}))

"""CensusRecorder (Observer): records both counterparties of every tracked-symbol trade.

Trades replayed after a websocket reconnect are ignored by (symbol, tid), remembered in a
bounded window so memory stays flat on a long-running census.
"""

from __future__ import annotations

from collections import OrderedDict

from hlsignals.domain.models import TapeTrade
from hlsignals.domain.symbols import Symbol
from hlsignals.wallets.census.registry import WalletRegistry


class CensusRecorder:
    def __init__(
        self, registry: WalletRegistry, tracked: frozenset[Symbol], dedupe_capacity: int
    ) -> None:
        if dedupe_capacity < 1:
            raise ValueError(f"dedupe capacity must be positive: {dedupe_capacity}")
        self._registry = registry
        self._tracked = tracked
        self._capacity = dedupe_capacity
        self._seen: OrderedDict[tuple[Symbol, int], None] = OrderedDict()

    def on_trade(self, trade: TapeTrade) -> None:
        if trade.symbol not in self._tracked:
            return
        key = (trade.symbol, trade.tid)
        if key in self._seen:
            return
        self._seen[key] = None
        if len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        self._registry.observe((trade.buyer, trade.seller), trade.time_ms)

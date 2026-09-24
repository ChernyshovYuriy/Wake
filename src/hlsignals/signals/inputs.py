"""Everything the signal layer needs for one ticker, assembled by the pipeline.

The signal layer is pure: calendar answers (last cash close, whether the session is open
now) arrive as values, and every wallet contributing a position or fill must be scored.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hlsignals.domain.models import Candle, Fill, MarketCtx, Position, ScoredWallet
from hlsignals.domain.symbols import Symbol


@dataclass(frozen=True, slots=True)
class TickerInputs:
    symbol: Symbol
    market: MarketCtx
    wallets: Mapping[str, ScoredWallet]  # every wallet that passed the filters
    positions: tuple[Position, ...]  # their current positions on ``symbol``
    fills: tuple[Fill, ...]  # their recent fills on ``symbol``
    candles: tuple[Candle, ...]  # perp candles (1h), sorted
    as_of_ms: int
    last_close_ms: int  # most recent US cash-session close at or before as_of
    session_open: bool  # the cash session is open at as_of

    def __post_init__(self) -> None:
        if self.market.symbol != self.symbol:
            raise ValueError(f"market symbol {self.market.symbol} is not {self.symbol}")
        items: tuple[Position | Fill, ...] = (*self.positions, *self.fills)
        for item in items:
            if item.symbol != self.symbol:
                raise ValueError(f"{type(item).__name__} for symbol {item.symbol} in {self.symbol}")
            if item.wallet not in self.wallets:
                raise ValueError(f"{type(item).__name__} from unscored wallet {item.wallet}")
        if self.last_close_ms > self.as_of_ms:
            raise ValueError("last close is after as_of")

    def trust(self, wallet: str) -> float:
        return self.wallets[wallet].trust

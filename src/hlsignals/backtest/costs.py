"""Cost models (Strategy): the round-trip cost of a trade as a fraction of notional."""

from __future__ import annotations

from typing import Protocol

_BPS = 10_000


class CostModel(Protocol):
    def round_trip(self, ticker: str) -> float: ...


class BpsCostModel:
    """Spread + slippage as a flat number of basis points per side (entry and exit)."""

    def __init__(self, bps_per_side: float) -> None:
        if bps_per_side < 0:
            raise ValueError(f"cost bps must be non-negative: {bps_per_side}")
        self.bps_per_side = bps_per_side

    def round_trip(self, ticker: str) -> float:
        return 2 * self.bps_per_side / _BPS

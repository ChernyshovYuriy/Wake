"""Price lookups over candle series that never look ahead."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence

from hlsignals.domain.models import Candle


def price_at(candles: Sequence[Candle], t_ms: int) -> float | None:
    """Close of the last candle that had closed by ``t_ms`` (None if none had)."""
    closes = [c.close_ms for c in candles]
    if closes != sorted(closes):
        raise ValueError("candles must be sorted by close time")
    idx = bisect_right(closes, t_ms)
    return candles[idx - 1].close if idx else None

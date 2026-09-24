"""Price lookups over candle series that never look ahead."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence

from hlsignals.domain.models import Candle


def price_at(candles: Sequence[Candle], t_ms: int) -> float | None:
    """Close of the last candle that had closed by ``t_ms`` (None if none had).

    Precondition: ``candles`` sorted by close time. Every producer guarantees it
    (``adapt_candles`` sorts, ``HistoricalData`` validates), so this is a binary search.
    """
    idx = bisect_right(candles, t_ms, key=lambda c: c.close_ms)
    return candles[idx - 1].close if idx else None

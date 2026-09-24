from __future__ import annotations

import pytest

from hlsignals.domain.candles import price_at
from tests.factories import HOUR_MS, T0_MS, make_candle_series

CANDLES = make_candle_series([100.0, 101.0, 102.0])  # closes at T0+1h-1, T0+2h-1, T0+3h-1


@pytest.mark.parametrize(
    ("t", "expected"),
    [
        (T0_MS, None),  # first candle not closed yet: no known price
        (T0_MS + HOUR_MS - 1, 100.0),  # exactly at its close
        (T0_MS + HOUR_MS + 5, 100.0),  # second candle still open
        (T0_MS + 10 * HOUR_MS, 102.0),  # after the last candle
    ],
)
def test_price_at_uses_only_closed_candles(t: int, expected: float | None) -> None:
    assert price_at(CANDLES, t) == expected


def test_price_at_empty() -> None:
    assert price_at([], T0_MS) is None


def test_price_at_requires_sorted_candles() -> None:
    with pytest.raises(ValueError, match="sorted"):
        price_at(list(reversed(CANDLES)), T0_MS + 10 * HOUR_MS)

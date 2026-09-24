"""AsOfView: the only gateway to historical data in a backtest (the look-ahead guard).

Signal construction receives an AsOfView at time t and can only see fills with
time <= t, candles closed by t, and prior scores taken by t. Asking for a window that
ends after t raises LookAheadError. Outcomes (forward stock returns) are measured by the
replay from the price book, never through a view.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field

from hlsignals.core.clock import to_ms
from hlsignals.core.errors import LookAheadError
from hlsignals.domain.models import Candle, Fill, MarketCtx, WalletRecord
from hlsignals.domain.symbols import Symbol


def _require_sorted(times: list[int], what: str) -> None:
    if times != sorted(times):
        raise ValueError(f"{what} must be sorted by time")


@dataclass(frozen=True, slots=True)
class HistoricalData:
    records: tuple[WalletRecord, ...]
    fills: Mapping[str, tuple[Fill, ...]]  # by wallet, sorted by time
    candles: Mapping[Symbol, tuple[Candle, ...]]  # perp 1h candles, sorted by close
    markets: Mapping[Symbol, MarketCtx]  # the universe, with a current snapshot
    _fill_times: dict[str, list[int]] = field(init=False, repr=False)
    _close_times: dict[Symbol, list[int]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        fill_times = {w: [f.time_ms for f in fs] for w, fs in self.fills.items()}
        close_times = {s: [c.close_ms for c in cs] for s, cs in self.candles.items()}
        for wallet, times in fill_times.items():
            _require_sorted(times, f"fills of {wallet}")
        for symbol, times in close_times.items():
            _require_sorted(times, f"candles of {symbol}")
        object.__setattr__(self, "_fill_times", fill_times)
        object.__setattr__(self, "_close_times", close_times)

    def at(self, t_ms: int) -> AsOfView:
        return AsOfView(self, t_ms)


class AsOfView:
    def __init__(self, data: HistoricalData, t_ms: int) -> None:
        self._data = data
        self.t_ms = t_ms

    def _check(self, end_ms: int) -> None:
        if end_ms > self.t_ms:
            raise LookAheadError(f"read up to {end_ms} from a view as of {self.t_ms}")

    def fills(self, wallet: str) -> tuple[Fill, ...]:
        fills = self._data.fills.get(wallet, ())
        return fills[: bisect_right(self._data._fill_times.get(wallet, []), self.t_ms)]

    def fills_between(self, wallet: str, start_ms: int, end_ms: int) -> tuple[Fill, ...]:
        """Fills with start_ms < time <= end_ms (end must not be after t)."""
        self._check(end_ms)
        return tuple(f for f in self.fills(wallet) if start_ms < f.time_ms <= end_ms)

    def candles(self, symbol: Symbol) -> tuple[Candle, ...]:
        candles = self._data.candles.get(symbol, ())
        return candles[: bisect_right(self._data._close_times.get(symbol, []), self.t_ms)]

    def candles_between(self, symbol: Symbol, start_ms: int, end_ms: int) -> tuple[Candle, ...]:
        """Candles opened at or after start_ms and closed by end_ms (end not after t)."""
        self._check(end_ms)
        return tuple(
            c for c in self.candles(symbol) if start_ms <= c.open_ms and c.close_ms <= end_ms
        )

    def score(self, record: WalletRecord) -> float | None:
        """The record's prior score, or None if it was taken after t."""
        return record.raw_score if to_ms(record.as_of) <= self.t_ms else None

    @property
    def records(self) -> tuple[WalletRecord, ...]:
        return self._data.records

    @property
    def markets(self) -> Mapping[Symbol, MarketCtx]:
        return self._data.markets

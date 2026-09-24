"""FIFO lot matching per symbol: closed lots, holding times, realized PnL, as-of position.

Fills must be fed in time order; fills sharing a millisecond keep the order they were fed
in (the API order), because ``tid`` is not monotonic (docs/api-notes.md §5).

Every fill reports ``start_position``. When it disagrees with the tracked position the
tracked state is wrong (history truncated or a fill is missing), so the open lots are
replaced by one lot of unknown origin with the reported size. Closing such a lot yields an
*orphan* ClosedLot: size and exit are known, entry and PnL are not, and are never guessed.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from hlsignals.core.clock import ms_to_days
from hlsignals.domain.direction import signed_size
from hlsignals.domain.models import Fill, PositionSide
from hlsignals.domain.symbols import Symbol

_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class ClosedLot:
    symbol: Symbol
    side: PositionSide
    size: Decimal  # always positive
    exit_px: Decimal
    exit_time_ms: int
    entry_px: Decimal | None  # None: opened before the observed history (orphan)
    entry_time_ms: int | None

    @property
    def is_orphan(self) -> bool:
        return self.entry_px is None

    @property
    def realized_pnl(self) -> Decimal | None:
        """Gross PnL (fees excluded, like the API's closedPnl); None for orphans."""
        if self.entry_px is None:
            return None
        direction = 1 if self.side is PositionSide.LONG else -1
        return (self.exit_px - self.entry_px) * self.size * direction

    @property
    def holding_ms(self) -> int | None:
        if self.entry_time_ms is None:
            return None
        return self.exit_time_ms - self.entry_time_ms

    @property
    def holding_days(self) -> float | None:
        held = self.holding_ms
        return None if held is None else ms_to_days(held)


@dataclass(frozen=True, slots=True)
class PositionGap:
    """A mid-history fill whose start_position disagreed with the tracked position."""

    symbol: Symbol
    time_ms: int
    tid: int
    tracked: Decimal
    reported: Decimal


@dataclass(slots=True)
class _OpenLot:
    size: Decimal  # signed
    entry_px: Decimal | None
    entry_time_ms: int | None


class LotBook:
    def __init__(self) -> None:
        self._lots: defaultdict[Symbol, deque[_OpenLot]] = defaultdict(deque)
        self._times: dict[Symbol, list[int]] = {}
        self._positions: dict[Symbol, list[Decimal]] = {}
        self._first_start: dict[Symbol, Decimal] = {}
        self._api_pnl: defaultdict[Symbol, Decimal] = defaultdict(Decimal)
        self._closed: list[ClosedLot] = []
        self._gaps: list[PositionGap] = []
        self._last_time_ms: int | None = None

    def add_all(self, fills: Iterable[Fill]) -> None:
        for fill in fills:
            self.add(fill)

    def add(self, fill: Fill) -> None:
        if self._last_time_ms is not None and fill.time_ms < self._last_time_ms:
            raise ValueError(
                f"fills must be fed in time order: {fill.time_ms} < {self._last_time_ms}"
            )
        delta = signed_size(fill)
        self._last_time_ms = fill.time_ms
        self._reconcile(fill)
        self._match(fill, delta)
        self._api_pnl[fill.symbol] += fill.closed_pnl
        self._times[fill.symbol].append(fill.time_ms)
        # After _reconcile the tracked position equals start_position; history is not rewritten.
        self._positions[fill.symbol].append(fill.start_position + delta)

    @property
    def closed_lots(self) -> tuple[ClosedLot, ...]:
        return tuple(self._closed)

    @property
    def gaps(self) -> tuple[PositionGap, ...]:
        return tuple(self._gaps)

    @property
    def symbols(self) -> frozenset[Symbol]:
        return frozenset(self._times)

    def position(self, symbol: Symbol) -> Decimal:
        """Current signed position (after every fill fed so far)."""
        positions = self._positions.get(symbol)
        return positions[-1] if positions else _ZERO

    def net_position(self, symbol: Symbol, as_of_ms: int) -> Decimal | None:
        """Signed position after all fills with time <= as_of_ms.

        None when as_of precedes the observed history and that history did not start flat.
        """
        times = self._times.get(symbol)
        if not times:
            return _ZERO
        idx = bisect_right(times, as_of_ms)
        if idx == 0:
            return _ZERO if self._first_start[symbol] == 0 else None
        return self._positions[symbol][idx - 1]

    def api_closed_pnl(self, symbol: Symbol) -> Decimal:
        """Sum of the API's closedPnl over fed fills (to cross-check realized PnL)."""
        return self._api_pnl[symbol]

    def _reconcile(self, fill: Fill) -> None:
        symbol = fill.symbol
        if symbol not in self._times:
            self._times[symbol] = []
            self._positions[symbol] = []
            self._first_start[symbol] = fill.start_position
        elif fill.start_position != self.position(symbol):
            self._gaps.append(
                PositionGap(
                    symbol, fill.time_ms, fill.tid, self.position(symbol), fill.start_position
                )
            )
        else:
            return
        lots = self._lots[symbol]
        lots.clear()
        if fill.start_position != 0:
            lots.append(_OpenLot(fill.start_position, None, None))

    def _match(self, fill: Fill, delta: Decimal) -> None:
        lots = self._lots[fill.symbol]
        remaining = delta
        while remaining != 0 and lots and (lots[0].size > 0) != (remaining > 0):
            lot = lots[0]
            taken = min(abs(remaining), abs(lot.size))
            self._closed.append(
                ClosedLot(
                    symbol=fill.symbol,
                    side=PositionSide.LONG if lot.size > 0 else PositionSide.SHORT,
                    size=taken,
                    exit_px=fill.px,
                    exit_time_ms=fill.time_ms,
                    entry_px=lot.entry_px,
                    entry_time_ms=lot.entry_time_ms,
                )
            )
            lot.size += taken if lot.size < 0 else -taken
            remaining += taken if remaining < 0 else -taken
            if lot.size == 0:
                lots.popleft()
        if remaining != 0:
            lots.append(_OpenLot(remaining, fill.px, fill.time_ms))

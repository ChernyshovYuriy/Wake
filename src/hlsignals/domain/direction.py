"""Fill ``dir`` -> signed exposure change. The only place ``dir`` values are interpreted.

Value set verified against live fills (docs/api-notes.md §4). For every perp ``dir``, the
sign is + exactly when ``side`` is BUY; a disagreement means the payload is not what we
think it is, so it is rejected rather than trusted.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from hlsignals.core.errors import AdapterError, NonPerpFillError
from hlsignals.domain.models import Fill, Side

PERP_DIR_SIGN: Final[Mapping[str, int]] = MappingProxyType(
    {
        "Open Long": 1,
        "Close Short": 1,
        "Short > Long": 1,
        "Close Long": -1,
        "Open Short": -1,
        "Long > Short": -1,
        # Liquidations close the position with a forced trade: a long is sold, a short
        # bought. All four observed live (2026-09-24/25; one real fill each in
        # tests/fixtures/hl/dir_catalog.json). The side cross-check in signed_size applies.
        "Liquidated Isolated Long": -1,
        "Liquidated Isolated Short": 1,
        "Liquidated Cross Long": -1,
        "Liquidated Cross Short": 1,
    }
)
NON_PERP_DIRS: Final[frozenset[str]] = frozenset(
    {"Buy", "Sell", "Merge Outcome", "Spot Dust Conversion"}
)
# A delisted market settles every open position at the settlement price: a forced full close.
# Its sign follows the position being closed, so it has no entry in PERP_DIR_SIGN. Observed
# 2026-09-25 (IP: start -937.5, buy 937.5, fee 0; tests/fixtures/hl/dir_catalog.json).
SETTLEMENT: Final = "Settlement"
# The exchange reduces a profitable position on the other side of a liquidation it cannot fill.
# Its sign also follows the position, and it may close only part of it. Observed 2026-09-25
# (CASHCAT: start 163454, sell 163454, fee 0, method "backstop"; dir_catalog.json).
AUTO_DELEVERAGING: Final = "Auto-Deleveraging"
FORCED_CLOSE_DIRS: Final[frozenset[str]] = frozenset({SETTLEMENT, AUTO_DELEVERAGING})


def is_perp_dir(direction: str) -> bool:
    return direction in PERP_DIR_SIGN or direction in FORCED_CLOSE_DIRS


def sign_of(direction: str) -> int:
    """+1 if the fill adds long exposure (buys), -1 if it adds short exposure (sells)."""
    sign = PERP_DIR_SIGN.get(direction)
    if sign is not None:
        return sign
    if direction in FORCED_CLOSE_DIRS:
        raise AdapterError(f"{direction!r}: the sign depends on the position it closes")
    if direction in NON_PERP_DIRS:
        raise NonPerpFillError(f"fill dir {direction!r} is not a perp fill")
    raise AdapterError(f"unknown fill dir {direction!r}")


def signed_size(fill: Fill) -> Decimal:
    if fill.dir in FORCED_CLOSE_DIRS:
        return _forced_close_size(fill)
    sign = sign_of(fill.dir)
    expected = Side.BUY if sign > 0 else Side.SELL
    if fill.side is not expected:
        raise AdapterError(f"fill side {fill.side!r} contradicts dir {fill.dir!r} (tid {fill.tid})")
    return fill.sz if sign > 0 else -fill.sz


def _forced_close_size(fill: Fill) -> Decimal:
    """A settlement closes the whole position; auto-deleveraging reduces it without flipping.
    Anything else is not a forced close we understand, so it is rejected rather than trusted."""
    delta = fill.sz if fill.side is Side.BUY else -fill.sz
    start, end = fill.start_position, fill.start_position + delta
    # Settlement: nothing left. ADL: 0 <= end/start < 1, i.e. smaller, same side, never flipped.
    ok = start != 0 and (end == 0 if fill.dir == SETTLEMENT else 0 <= end / start < 1)
    if not ok:
        what = "close" if fill.dir == SETTLEMENT else "reduce"
        raise AdapterError(
            f"{fill.dir.lower()} fill does not {what} the position: start {start}, "
            f"{fill.side.name} {fill.sz} (tid {fill.tid})"
        )
    return delta


def signed_notional(fill: Fill) -> float:
    """USD exposure change: + long / - short; magnitude px * sz."""
    return float(signed_size(fill) * fill.px)

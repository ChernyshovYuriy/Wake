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
    }
)
NON_PERP_DIRS: Final[frozenset[str]] = frozenset(
    {"Buy", "Sell", "Merge Outcome", "Spot Dust Conversion"}
)


def is_perp_dir(direction: str) -> bool:
    return direction in PERP_DIR_SIGN


def sign_of(direction: str) -> int:
    """+1 if the fill adds long exposure (buys), -1 if it adds short exposure (sells)."""
    sign = PERP_DIR_SIGN.get(direction)
    if sign is not None:
        return sign
    if direction in NON_PERP_DIRS:
        raise NonPerpFillError(f"fill dir {direction!r} is not a perp fill")
    raise AdapterError(f"unknown fill dir {direction!r}")


def signed_size(fill: Fill) -> Decimal:
    sign = sign_of(fill.dir)
    expected = Side.BUY if sign > 0 else Side.SELL
    if fill.side is not expected:
        raise AdapterError(f"fill side {fill.side!r} contradicts dir {fill.dir!r} (tid {fill.tid})")
    return fill.sz if sign > 0 else -fill.sz


def signed_notional(fill: Fill) -> float:
    """USD exposure change: + long / - short; magnitude px * sz."""
    return float(signed_size(fill) * fill.px)

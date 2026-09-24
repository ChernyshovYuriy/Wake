"""Wallet address validation: ``0x`` + 40 hex, canonical form lower-case."""

from __future__ import annotations

import re

from hlsignals.core.errors import AdapterError

_ADDRESS_RE = re.compile(r"0x[0-9a-f]{40}")


def normalize_address(raw: str) -> str:
    """Canonical (lower-case) form of a wallet address; raises AdapterError if invalid."""
    candidate = raw.lower()
    if not _ADDRESS_RE.fullmatch(candidate):
        raise AdapterError(f"invalid wallet address: {raw!r}")
    return candidate


def require_address(address: str) -> None:
    """Raise AdapterError unless ``address`` is already canonical."""
    if not _ADDRESS_RE.fullmatch(address):
        raise AdapterError(f"wallet address not canonical (0x + 40 lower-case hex): {address!r}")

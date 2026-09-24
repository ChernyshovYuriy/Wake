"""Symbol value object: the only place that parses or formats ``"<dex>:<coin>"``.

HIP-3 markets carry a dex prefix (``xyz:NVDA``). Core crypto perps (``BTC``), spot
(``@142``, ``PURR/USDC``) and outcome (``#25510``) coins have none; they are parsed only so
callers can recognise and drop them. The dex is lower-cased; coin case is kept as the API
returns it (docs/api-notes.md §2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hlsignals.core.errors import AdapterError

_SEPARATOR = ":"
_DEX_RE = re.compile(r"[a-z0-9]+")
_COIN_RE = re.compile(r"\S+")


@dataclass(frozen=True, slots=True, order=True)
class Symbol:
    dex: str | None
    coin: str

    def __post_init__(self) -> None:
        if self.dex is not None and not _DEX_RE.fullmatch(self.dex):
            raise AdapterError(f"invalid dex {self.dex!r} (expected lower-case alphanumerics)")
        if not _COIN_RE.fullmatch(self.coin) or _SEPARATOR in self.coin:
            raise AdapterError(f"invalid coin {self.coin!r}")

    @classmethod
    def parse(cls, raw: str) -> Symbol:
        parts = raw.split(_SEPARATOR)
        if len(parts) == 1:
            return cls(None, raw)
        if len(parts) != 2 or not parts[0] or not parts[1]:  # noqa: PLR2004 - dex + coin
            raise AdapterError(f"malformed symbol {raw!r}")
        return cls(parts[0].lower(), parts[1])

    @property
    def is_hip3(self) -> bool:
        return self.dex is not None

    def __str__(self) -> str:
        return self.coin if self.dex is None else f"{self.dex}{_SEPARATOR}{self.coin}"

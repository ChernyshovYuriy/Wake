"""Corroboration rule: a ticker is scored only when at least ``min_wallets`` distinct
wallets with trust >= ``min_trust`` hold a non-zero position in it now, or traded it in the
last ``window_hours`` (as_of - window, as_of]. Older trades do not count: a wallet that is
flat and traded months ago says nothing about the ticker today."""

from __future__ import annotations

from dataclasses import dataclass

from hlsignals.core.clock import MS_PER_HOUR
from hlsignals.signals.inputs import TickerInputs


@dataclass(frozen=True, slots=True)
class CorroborationResult:
    sufficient: bool
    wallets: frozenset[str]
    reason: str

    @property
    def n_wallets(self) -> int:
        return len(self.wallets)


class Corroboration:
    def __init__(self, min_wallets: int, min_trust: float, window_hours: float) -> None:
        if min_wallets < 1:
            raise ValueError(f"min_wallets must be >= 1: {min_wallets}")
        if not 0 <= min_trust <= 1:
            raise ValueError(f"min_trust must be in [0, 1]: {min_trust}")
        if window_hours <= 0:
            raise ValueError(f"window_hours must be positive: {window_hours}")
        self.min_wallets = min_wallets
        self.min_trust = min_trust
        self.window_ms = int(window_hours * MS_PER_HOUR)

    def check(self, inputs: TickerInputs) -> CorroborationResult:
        involved = {p.wallet for p in inputs.positions if p.size != 0}
        start = inputs.as_of_ms - self.window_ms
        involved |= {f.wallet for f in inputs.fills if start < f.time_ms <= inputs.as_of_ms}
        trusted = frozenset(w for w in involved if inputs.trust(w) >= self.min_trust)
        n = len(trusted)
        detail = f"{len(involved)} involved, trust >= {self.min_trust}"
        if n >= self.min_wallets:
            return CorroborationResult(
                True, trusted, f"{n} trusted wallets >= {self.min_wallets} ({detail})"
            )
        return CorroborationResult(
            False, trusted, f"{n} trusted wallets < {self.min_wallets} ({detail})"
        )

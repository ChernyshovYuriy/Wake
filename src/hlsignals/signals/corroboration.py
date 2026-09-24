"""Corroboration rule: a ticker is scored only when at least ``min_wallets`` distinct
wallets with trust >= ``min_trust`` hold a non-zero position in it or traded it."""

from __future__ import annotations

from dataclasses import dataclass

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
    def __init__(self, min_wallets: int, min_trust: float) -> None:
        if min_wallets < 1:
            raise ValueError(f"min_wallets must be >= 1: {min_wallets}")
        if not 0 <= min_trust <= 1:
            raise ValueError(f"min_trust must be in [0, 1]: {min_trust}")
        self.min_wallets = min_wallets
        self.min_trust = min_trust

    def check(self, inputs: TickerInputs) -> CorroborationResult:
        involved = {p.wallet for p in inputs.positions if p.size != 0}
        involved |= {f.wallet for f in inputs.fills}
        trusted = frozenset(w for w in involved if inputs.trust(w) >= self.min_trust)
        n = len(trusted)
        if n >= self.min_wallets:
            return CorroborationResult(True, trusted, f"{n} trusted wallets >= {self.min_wallets}")
        return CorroborationResult(
            False,
            trusted,
            f"{n} trusted wallets < {self.min_wallets} (trust >= {self.min_trust})",
        )

"""``vet``: fetch each wallet's history, run the wallet filters and the scorer, explain.

Every wallet is scored even when a filter rejects it, so the report shows both the
rejection reason and what the score would have been. An API error on one wallet is
reported for that wallet and the rest continue.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol

from hlsignals.app.config import VetSettings
from hlsignals.core.chain import FilterChain
from hlsignals.core.clock import Clock, from_ms, to_ms
from hlsignals.core.errors import AdapterError, TransportError
from hlsignals.domain.models import Candle, Fill, ScoredWallet, WalletRecord
from hlsignals.domain.symbols import Symbol
from hlsignals.reporting.evidence import fmt_evidence
from hlsignals.wallets.scoring.scorer import WalletScorer
from hlsignals.wallets.scoring.slice import EquitySlice


class FillHistory(Protocol):
    truncated: bool

    def __iter__(self) -> Iterator[Fill]: ...


class VetGateway(Protocol):
    def user_fills_by_time(
        self, user: str, start: datetime, end: datetime | None
    ) -> FillHistory: ...

    def candle_snapshot(
        self, symbol: Symbol, interval: str, start: datetime, end: datetime
    ) -> list[Candle]: ...


@dataclass(frozen=True, slots=True)
class VetResult:
    record: WalletRecord
    scored: ScoredWallet | None
    rejection: tuple[str, str] | None  # (filter name, reason)
    error: str | None
    n_fills: int
    n_trips: int
    truncated: bool

    @property
    def accepted(self) -> bool:
        return self.error is None and self.rejection is None


class WalletVetter:
    def __init__(
        self,
        *,
        gateway: VetGateway,
        clock: Clock,
        equities: frozenset[Symbol],
        chain: FilterChain[EquitySlice],
        scorer: WalletScorer,
        settings: VetSettings,
    ) -> None:
        self._gateway = gateway
        self._clock = clock
        self._equities = equities
        self._chain = chain
        self._scorer = scorer
        self._settings = settings

    def vet_all(self, records: Iterable[WalletRecord]) -> list[VetResult]:
        results = [self._vet(record) for record in records]
        return sorted(results, key=_rank)

    def _vet(self, record: WalletRecord) -> VetResult:
        try:
            wallet = self._slice(record)
        except (TransportError, AdapterError) as exc:
            return VetResult(record, None, None, f"{type(exc).__name__}: {exc}", 0, 0, False)
        rejection = self._chain.evaluate(wallet)
        return VetResult(
            record=record,
            scored=self._scorer.score(wallet),
            rejection=None if rejection is None else (rejection.filter_name, rejection.reason),
            error=None,
            n_fills=len(wallet.fills),
            n_trips=wallet.n_scored_trips,
            truncated=wallet.truncated,
        )

    def _slice(self, record: WalletRecord) -> EquitySlice:
        now = self._clock.now()
        start = now - timedelta(days=self._settings.lookback_days)
        history = self._gateway.user_fills_by_time(record.address, start, now)
        wallet = EquitySlice.from_history(
            record.address,
            list(history),
            positions=(),
            equities=self._equities,
            as_of_ms=to_ms(now),
            raw_score=record.raw_score,
            truncated=history.truncated,
        )
        first_fill: dict[Symbol, int] = {}
        for fill in wallet.fills:
            first_fill.setdefault(fill.symbol, fill.time_ms)
        candles = {
            symbol: self._gateway.candle_snapshot(
                symbol, self._settings.candle_interval, from_ms(first_ms), now
            )
            for symbol, first_ms in first_fill.items()
        }
        return replace(wallet, candles=candles)


def _rank(result: VetResult) -> tuple[int, float]:
    group = 2 if result.error else (0 if result.accepted else 1)
    trust = result.scored.trust if result.scored else 0.0
    return group, -trust


def render_vet(results: Sequence[VetResult]) -> str:
    if not results:
        return "no wallets to vet\n"
    return "".join(_render_one(r) for r in results)


def _render_one(result: VetResult) -> str:
    record = result.record
    lines = [f"{record.address}  (source: {record.source})"]
    if result.error:
        lines.append(f"  ERROR {result.error}")
        return "\n".join(lines) + "\n\n"
    verdict = (
        "ACCEPTED" if result.rejection is None else "REJECTED {}: {}".format(*result.rejection)
    )
    lines.append(f"  {verdict}")
    scored = result.scored
    if scored is not None:
        lines.append(
            f"  trust {scored.trust:.3f} = base x confidence {scored.confidence:.3f}"
            f" x decay {scored.decay:.3f}   round trips {result.n_trips}, US-stock fills"
            f" {result.n_fills}, track record {scored.track_record_days:.1f} days"
            + ("   [history truncated by API cap]" if result.truncated else "")
        )
        for name, feature in scored.features.items():
            lines.append(f"    {name:<12} {feature.value:.3f}   {fmt_evidence(feature.evidence)}")
    return "\n".join(lines) + "\n\n"

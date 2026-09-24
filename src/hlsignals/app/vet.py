"""``vet``: fetch each wallet's history, run the wallet filters and the scorer, explain.

Every wallet is scored even when a filter rejects it, so the report shows both the
rejection reason and what the score would have been. An API error on one wallet is
reported for that wallet and the rest continue.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import islice
from typing import Protocol

from hlsignals.app.candles import CandleCache, LazyCandles
from hlsignals.app.config import HistorySettings
from hlsignals.core.chain import FilterChain
from hlsignals.core.clock import to_ms
from hlsignals.core.errors import AdapterError, TransportError
from hlsignals.domain.models import Fill, ScoredWallet, WalletRecord
from hlsignals.domain.symbols import Symbol
from hlsignals.reporting.evidence import fmt_evidence
from hlsignals.wallets.filters import prescreen_fill_rate
from hlsignals.wallets.scoring.scorer import WalletScorer
from hlsignals.wallets.scoring.slice import EquitySlice

logger = logging.getLogger(__name__)
_PROGRESS_EVERY = 10


class FillHistory(Protocol):
    truncated: bool

    def __iter__(self) -> Iterator[Fill]: ...


class FillsPort(Protocol):
    def user_fills_by_time(
        self, user: str, start: datetime, end: datetime | None
    ) -> FillHistory: ...


@dataclass(frozen=True, slots=True)
class Prescreen:
    """Reject heavy market makers from their first page of fills (see prescreen_fill_rate)."""

    page_size: int  # the API page cap: only a full first page is judged
    max_fills_per_day: float


@dataclass(frozen=True, slots=True)
class Prescreened:
    """A heavy wallet rejected from its first page of fills; the rest was not fetched."""

    reason: str
    n_fills: int


@dataclass(frozen=True, slots=True)
class FetchedHistory:
    fills: list[Fill]
    truncated: bool


def fetch_history(
    port: FillsPort,
    address: str,
    start: datetime,
    end: datetime,
    *,
    equities: frozenset[Symbol],
    prescreen: Prescreen | None,
) -> FetchedHistory | Prescreened:
    """A wallet's fills in [start, end]; heavy market makers stop after one page."""
    history = port.user_fills_by_time(address, start, end)
    fills = iter(history)
    head = list(islice(fills, prescreen.page_size)) if prescreen else []
    if prescreen and len(head) == prescreen.page_size:
        sample = [f for f in head if f.symbol in equities]
        verdict = prescreen_fill_rate(sample, prescreen.max_fills_per_day)
        if not verdict.accepted:
            return Prescreened(verdict.reason, len(sample))
    return FetchedHistory([*head, *fills], history.truncated)


@dataclass(frozen=True, slots=True)
class VetResult:
    record: WalletRecord
    scored: ScoredWallet | None
    rejection: tuple[str, str] | None  # (filter name, reason)
    error: str | None
    n_fills: int = 0
    n_trips: int = 0
    truncated: bool = False
    # The full history is kept only for accepted wallets (the pipeline needs their fills);
    # rejected market makers can carry 10k fills each, so theirs is dropped.
    wallet: EquitySlice | None = None

    @property
    def accepted(self) -> bool:
        return self.error is None and self.rejection is None


class WalletVetter:
    def __init__(
        self,
        *,
        fills: FillsPort,
        candles: CandleCache,
        equities: frozenset[Symbol],
        chain: FilterChain[EquitySlice],
        scorer: WalletScorer,
        settings: HistorySettings,
        prescreen: Prescreen | None = None,
    ) -> None:
        self._fills = fills
        self._candles = candles
        self._equities = equities
        self._chain = chain
        self._scorer = scorer
        self._settings = settings
        self._prescreen = prescreen

    def vet_all(self, records: Iterable[WalletRecord], as_of: datetime) -> list[VetResult]:
        results = []
        for i, record in enumerate(records, start=1):
            results.append(self._vet(record, as_of))
            if i % _PROGRESS_EVERY == 0:
                logger.info("vetted %d wallets", i)
        return sorted(results, key=_rank)

    def _vet(self, record: WalletRecord, as_of: datetime) -> VetResult:
        try:
            wallet = self._slice(record, as_of)
        except (TransportError, AdapterError) as exc:
            return VetResult(record, None, None, f"{type(exc).__name__}: {exc}")
        if isinstance(wallet, Prescreened):
            rejection_reason = ("maker_profile", wallet.reason)
            return VetResult(record, None, rejection_reason, None, n_fills=wallet.n_fills)
        rejection = self._chain.evaluate(wallet)
        return VetResult(
            record=record,
            scored=self._scorer.score(wallet),
            rejection=None if rejection is None else (rejection.filter_name, rejection.reason),
            error=None,
            n_fills=len(wallet.fills),
            n_trips=wallet.n_scored_trips,
            truncated=wallet.truncated,
            wallet=wallet if rejection is None else None,
        )

    def _slice(self, record: WalletRecord, as_of: datetime) -> EquitySlice | Prescreened:
        start = as_of - timedelta(days=self._settings.lookback_days)
        fetched = fetch_history(
            self._fills,
            record.address,
            start,
            as_of,
            equities=self._equities,
            prescreen=self._prescreen,
        )
        if isinstance(fetched, Prescreened):
            return fetched
        wallet = EquitySlice.from_history(
            record.address,
            fetched.fills,
            positions=(),
            equities=self._equities,
            as_of_ms=to_ms(as_of),
            raw_score=record.raw_score,
            truncated=fetched.truncated,
        )
        traded = frozenset(fill.symbol for fill in wallet.fills)
        return replace(wallet, candles=LazyCandles(self._candles, traded, start, as_of))


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

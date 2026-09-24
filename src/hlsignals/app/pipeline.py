"""SignalPipeline: orchestration only. Every rule it applies lives in the module it calls.

    discover markets -> filter markets -> fetch wallets -> vet (history, filters, score)
    -> positions of accepted wallets -> per-ticker inputs -> signals -> rank -> report

Failures that affect one wallet or one symbol are recorded in the report's diagnostics
and the run continues. Configuration errors and "every wallet source failed" propagate.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from hlsignals.app.candles import CandleCache, CandlePort
from hlsignals.app.config import Settings
from hlsignals.app.vet import FillsPort, Prescreen, VetResult, WalletVetter
from hlsignals.core.chain import ChainResult, FilterChain
from hlsignals.core.clock import to_ms
from hlsignals.core.errors import AdapterError, TransportError
from hlsignals.domain.models import (
    Candle,
    Diagnostics,
    MarketCtx,
    Position,
    ScoredWallet,
    SignalReport,
    WalletRecord,
)
from hlsignals.domain.symbols import Symbol
from hlsignals.session.calendar import SessionCalendar
from hlsignals.signals.engine import SignalEngine
from hlsignals.signals.inputs import TickerInputs
from hlsignals.signals.ranker import rank
from hlsignals.universe.discovery import Discovery, EquityDexLocator, MarketDataPort
from hlsignals.wallets.scoring.scorer import WalletScorer
from hlsignals.wallets.scoring.slice import EquitySlice
from hlsignals.wallets.sources.base import SourceResult, WalletSourcePort

API_ERRORS = (TransportError, AdapterError)
logger = logging.getLogger(__name__)


class PipelineGateway(MarketDataPort, FillsPort, CandlePort, Protocol):
    def clearinghouse_state(self, user: str, dex: str | None) -> list[Position]: ...


@dataclass(frozen=True, slots=True)
class PipelineParts:
    settings: Settings
    gateway: PipelineGateway
    calendar: SessionCalendar
    locator: EquityDexLocator
    market_chain: FilterChain[MarketCtx]
    wallet_source: WalletSourcePort
    equities: frozenset[Symbol]
    wallet_chain: FilterChain[EquitySlice]
    scorer: WalletScorer
    engine: SignalEngine


@dataclass(frozen=True, slots=True)
class _Positions:
    by_wallet: dict[str, list[Position]]
    failed: dict[str, str]  # wallet -> error


class SignalPipeline:
    def __init__(self, parts: PipelineParts) -> None:
        self._p = parts

    def fetch_wallets(self) -> SourceResult:
        return self._p.wallet_source.fetch()

    def vet(self, records: Sequence[WalletRecord], as_of: datetime) -> list[VetResult]:
        return self._vetter(self._candles()).vet_all(records, as_of)

    def run(self, as_of: datetime, sources: SourceResult | None = None) -> SignalReport:
        """Build the report as of ``as_of``; ``sources`` overrides the wallet fetch."""
        candles = self._candles()
        discovery = self._p.locator.locate()
        markets = self._p.market_chain.run(discovery.markets)
        logger.info(
            "universe: %d markets discovered, %d after filters",
            len(discovery.markets),
            len(markets.accepted),
        )
        sources = sources if sources is not None else self.fetch_wallets()
        logger.info("vetting %d wallets", len(sources.records))
        vetted = self._vetter(candles).vet_all(sources.records, as_of)
        accepted = [v for v in vetted if v.accepted]
        logger.info("%d wallets accepted; fetching their positions", len(accepted))
        dexes = sorted({m.symbol.dex for m in markets.accepted if m.symbol.dex})
        positions = self._positions(accepted, dexes)
        logger.info("computing signals for %d markets", len(markets.accepted))
        wallets = [v for v in accepted if v.record.address not in positions.failed]
        notes: list[str] = [
            f"positions unavailable for {w}: {e}" for w, e in positions.failed.items()
        ]
        signals = []
        for market in markets.accepted:
            symbol_candles, note = self._signal_candles(candles, market.symbol, as_of)
            notes += [note] if note else []
            signals.append(
                self._p.engine.evaluate(
                    self._inputs(market, wallets, positions, symbol_candles, as_of)
                )
            )
        scored = [v.scored for v in wallets if v.scored is not None]
        return SignalReport(
            as_of=as_of,
            signals=tuple(rank(signals)),
            wallets=tuple(sorted(scored, key=lambda w: (-w.trust, w.address))),
            diagnostics=self._diagnostics(
                sources=sources,
                vetted=vetted,
                n_accepted=len(wallets),
                positions=positions,
                discovery=discovery,
                markets=markets,
                notes=notes,
            ),
        )

    def _candles(self) -> CandleCache:
        return CandleCache(self._p.gateway, self._p.settings.history.candle_interval)

    def _vetter(self, candles: CandleCache) -> WalletVetter:
        return WalletVetter(
            fills=self._p.gateway,
            candles=candles,
            equities=self._p.equities,
            chain=self._p.wallet_chain,
            scorer=self._p.scorer,
            settings=self._p.settings.history,
            prescreen=self._prescreen(),
        )

    def _prescreen(self) -> Prescreen | None:
        """Pre-screen heavy wallets only when the maker filter is part of the chain."""
        s = self._p.settings
        if "maker_profile" not in s.wallet_filters.order:
            return None
        return Prescreen(s.api.fills_page_cap, s.wallet_filters.max_fills_per_day)

    def _positions(self, accepted: Sequence[VetResult], dexes: Sequence[str]) -> _Positions:
        by_wallet: dict[str, list[Position]] = {}
        failed: dict[str, str] = {}
        for result in accepted:
            address = result.record.address
            try:
                by_wallet[address] = [
                    p for dex in dexes for p in self._p.gateway.clearinghouse_state(address, dex)
                ]
            except API_ERRORS as exc:
                failed[address] = f"{type(exc).__name__}: {exc}"
        return _Positions(by_wallet, failed)

    def _signal_candles(
        self, candles: CandleCache, symbol: Symbol, as_of: datetime
    ) -> tuple[tuple[Candle, ...], str | None]:
        start = as_of - timedelta(hours=self._p.settings.history.signal_candle_hours)
        try:
            return candles.window(symbol, start, as_of), None
        except API_ERRORS as exc:
            return (), f"candles unavailable for {symbol}: {type(exc).__name__}: {exc}"

    def _inputs(
        self,
        market: MarketCtx,
        wallets: Sequence[VetResult],
        positions: _Positions,
        candles: tuple[Candle, ...],
        as_of: datetime,
    ) -> TickerInputs:
        symbol = market.symbol
        scored: dict[str, ScoredWallet] = {
            v.record.address: v.scored for v in wallets if v.scored is not None
        }
        return TickerInputs(
            symbol=symbol,
            market=market,
            wallets=scored,
            positions=tuple(
                p for w in scored for p in positions.by_wallet.get(w, ()) if p.symbol == symbol
            ),
            fills=tuple(
                f for v in wallets if v.wallet for f in v.wallet.fills if f.symbol == symbol
            ),
            candles=candles,
            as_of_ms=to_ms(as_of),
            last_close_ms=to_ms(self._p.calendar.last_close(as_of)),
            session_open=self._p.calendar.is_open(as_of),
        )

    @staticmethod
    def _diagnostics(
        *,
        sources: SourceResult,
        vetted: Sequence[VetResult],
        n_accepted: int,
        positions: _Positions,
        discovery: Discovery,
        markets: ChainResult[MarketCtx],
        notes: list[str],
    ) -> Diagnostics:
        rejected: Counter[str] = Counter()
        for result in vetted:
            if result.error is not None:
                rejected["api_error"] += 1
            elif result.rejection is not None:
                rejected[result.rejection[0]] += 1
        if positions.failed:
            rejected["positions_error"] += len(positions.failed)
        return Diagnostics(
            sources_used=sources.used,
            sources_failed=sources.failed,
            source_messages=tuple(f"{d.source}: {d.message}" for d in sources.diagnostics),
            wallets_considered=len(vetted),
            wallets_accepted=n_accepted,
            wallets_rejected=dict(rejected),
            wallets_truncated=sum(1 for v in vetted if v.truncated),
            universe_discovered=len(discovery.markets),
            universe_after_filters=len(markets.accepted),
            markets_rejected=markets.counts_by_filter(),
            unclassified_symbols=tuple(str(s) for s in discovery.unclassified),
            dex_failures=dict(discovery.dex_failures),
            notes=tuple(notes),
        )

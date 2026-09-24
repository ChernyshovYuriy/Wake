"""Backtest orchestration: load history once, then replay (and walk forward).

Loading is the only step that touches the network. Everything after it runs on the
loaded HistoricalData through AsOfViews, so a loaded dataset can be replayed with any
parameters. Per-item failures (a wallet's fills, a symbol's candles, a ticker's bars)
become caveats in the report instead of stopping the run.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from hlsignals.app.config import Settings
from hlsignals.app.pipeline import PipelineGateway
from hlsignals.app.vet import Prescreen, Prescreened, fetch_history
from hlsignals.app.wiring import (
    build_market_filter_chain,
    build_signal_engine,
    build_wallet_filter_chain,
    build_wallet_scorer,
)
from hlsignals.backtest.asof import HistoricalData
from hlsignals.backtest.costs import BpsCostModel
from hlsignals.backtest.prices import PriceBook, PriceHistory, ticker_for
from hlsignals.backtest.replay import Replay, ReplayResult
from hlsignals.backtest.report import BacktestReport
from hlsignals.backtest.signal_source import HistoricalSignalSource
from hlsignals.backtest.walkforward import WalkForward, make_folds
from hlsignals.core.clock import MS_PER_HOUR, to_ms
from hlsignals.core.errors import AdapterError, TransportError
from hlsignals.domain.models import Candle, DailyBar, Fill, MarketCtx, WalletRecord
from hlsignals.domain.symbols import Symbol
from hlsignals.session.calendar import SessionCalendar
from hlsignals.universe.discovery import EquityDexLocator
from hlsignals.wallets.sources.base import WalletSourcePort

API_ERRORS = (TransportError, AdapterError)
logger = logging.getLogger(__name__)

STANDING_CAVEATS = (
    "open interest has no history: today's contract count is used at each date's price",
    "wallets come from today's sources (census/curated): selection bias toward wallets active now",
    "the universe is today's market list: markets delisted earlier are missing (survivorship bias)",
    "positions opened before the lookback and not traded inside it are invisible",
    "overlapping trades (a new signal every session, 5-session holds) are not independent",
)


@dataclass(frozen=True, slots=True)
class LoadedHistory:
    data: HistoricalData
    prices: PriceBook
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SignalParams:
    min_trust: float
    epsilon: float

    def __str__(self) -> str:
        return f"min_trust={self.min_trust:g} epsilon={self.epsilon:g}"


def load_history(
    *,
    settings: Settings,
    gateway: PipelineGateway,
    locator: EquityDexLocator,
    wallet_source: WalletSourcePort,
    equities: frozenset[Symbol],
    prices: PriceHistory,
    start: date,
    now: datetime,
) -> LoadedHistory:
    history_start = datetime.combine(start, time(), tzinfo=now.tzinfo) - timedelta(
        days=settings.history.lookback_days
    )
    loader = _Loader(settings, gateway, history_start, now)
    markets = {
        m.symbol: m for m in locator.locate().markets if m.symbol in equities and not m.is_delisted
    }
    records, fills = loader.fills(wallet_source, equities)
    logger.info(
        "loaded fills of %d wallets; fetching candles for %d markets", len(records), len(markets)
    )
    candles = loader.candles(markets)
    logger.info("fetching daily stock bars")
    bars = loader.bars(prices, markets, start)
    data = HistoricalData(records=records, fills=fills, candles=candles, markets=markets)
    return LoadedHistory(data, PriceBook(bars), tuple(loader.notes))


class _Loader:
    def __init__(
        self, settings: Settings, gateway: PipelineGateway, start: datetime, now: datetime
    ) -> None:
        self._settings = settings
        self._gateway = gateway
        self._start = start
        self._now = now
        self.notes: list[str] = []

    def fills(
        self, wallet_source: WalletSourcePort, equities: frozenset[Symbol]
    ) -> tuple[tuple[WalletRecord, ...], dict[str, tuple[Fill, ...]]]:
        s = self._settings
        prescreen = Prescreen(s.api.fills_page_cap, s.wallet_filters.max_fills_per_day)
        records: list[WalletRecord] = []
        fills: dict[str, tuple[Fill, ...]] = {}
        prescreened = failed = 0
        for record in wallet_source.fetch().records:
            try:
                fetched = fetch_history(
                    self._gateway,
                    record.address,
                    self._start,
                    self._now,
                    equities=equities,
                    prescreen=prescreen,
                )
            except API_ERRORS:
                failed += 1
                continue
            if isinstance(fetched, Prescreened):
                prescreened += 1
                continue
            records.append(record)
            fills[record.address] = tuple(fetched.fills)
        if prescreened:
            self.notes.append(
                f"{prescreened} wallets dropped by the market-maker prescreen at load"
            )
        if failed:
            self.notes.append(f"{failed} wallets skipped: fills could not be fetched")
        return tuple(records), fills

    def candles(self, markets: dict[Symbol, MarketCtx]) -> dict[Symbol, tuple[Candle, ...]]:
        interval = self._settings.history.candle_interval
        candles: dict[Symbol, tuple[Candle, ...]] = {}
        late: list[str] = []
        for symbol in markets:
            try:
                rows = self._gateway.candle_snapshot(symbol, interval, self._start, self._now)
            except API_ERRORS as exc:
                self.notes.append(f"no candles for {symbol}: {type(exc).__name__}")
                continue
            candles[symbol] = tuple(sorted(rows, key=lambda c: c.close_ms))
            if rows and min(c.open_ms for c in rows) > to_ms(self._start) + MS_PER_HOUR:
                late.append(str(symbol))
        if late:
            self.notes.append(
                "candle history starts after the requested start (API depth cap or listing "
                f"date) for {len(late)} markets: {', '.join(sorted(late))}"
            )
        return candles

    def bars(
        self, prices: PriceHistory, markets: dict[Symbol, MarketCtx], start: date
    ) -> dict[str, list[DailyBar]]:
        bars: dict[str, list[DailyBar]] = {}
        missing: list[str] = []
        for symbol in markets:
            ticker = ticker_for(symbol, self._settings.backtest.ticker_overrides)
            try:
                bars[ticker] = prices.daily_bars(ticker, start, self._now.date())
            except API_ERRORS as exc:
                self.notes.append(f"no stock bars for {ticker}: {type(exc).__name__}")
                continue
            if not bars[ticker]:
                missing.append(ticker)
        if missing:
            self.notes.append(f"no stock bars returned for: {', '.join(sorted(missing))}")
        return bars


def run_backtest(
    *,
    settings: Settings,
    loaded: LoadedHistory,
    calendar: SessionCalendar,
    equities: frozenset[Symbol],
    start: date,
    end: date,
    walk_forward: bool,
) -> BacktestReport:
    bt = settings.backtest
    days = [s.day for s in calendar.sessions(start, end)]
    logger.info("replaying %d sessions%s", len(days), " with walk-forward" if walk_forward else "")
    source = HistoricalSignalSource(
        equities=equities,
        wallet_chain=build_wallet_filter_chain(settings.wallet_filters),
        scorer=build_wallet_scorer(settings.scoring),
        engine=build_signal_engine(settings.signals),
        market_chain=build_market_filter_chain(settings.universe),
        calendar=calendar,
        lookback_days=settings.history.lookback_days,
        signal_candle_hours=settings.history.signal_candle_hours,
    )

    def replay(params: SignalParams, run_days: Sequence[date]) -> ReplayResult:
        signals = dataclasses.replace(
            settings.signals, min_trust=params.min_trust, epsilon=params.epsilon
        )
        return Replay(
            data=loaded.data,
            source=source.with_engine(build_signal_engine(signals)),
            prices=loaded.prices,
            calendar=calendar,
            costs=BpsCostModel(bt.cost_bps_per_side),
            horizon_sessions=bt.horizon_sessions,
            preopen_minutes=bt.preopen_minutes,
            ticker_overrides=bt.ticker_overrides,
        ).run(run_days)

    configured = SignalParams(settings.signals.min_trust, settings.signals.epsilon)
    wf = None
    skipped: list[str] = []
    if walk_forward:
        grid = [SignalParams(t, e) for t in bt.min_trust_grid for e in bt.epsilon_grid]
        folds = make_folds(
            days, train=bt.train_sessions, test=bt.test_sessions, embargo=bt.horizon_sessions - 1
        )
        if folds:
            wf = WalkForward(folds, grid, replay).run()
        else:  # an empty walk-forward would report an empty "out-of-sample" result
            skipped.append(
                f"walk-forward skipped: {len(days)} sessions, at least {bt.train_sessions + 1} "
                "needed for one fold; results are in-sample"
            )
    single = replay(configured, days)
    excluded = source.excluded_wallets
    exclusion = (
        [
            f"{len(excluded)} wallets excluded: their history could not be interpreted "
            f"(first: {next(iter(excluded.values()))})"
        ]
        if excluded
        else []
    )
    return BacktestReport(
        start=start,
        end=end,
        horizon_sessions=bt.horizon_sessions,
        cost_bps_per_side=bt.cost_bps_per_side,
        min_trades_for_verdict=bt.min_trades_for_verdict,
        parameters=str(configured),
        single=single,
        walk_forward=wf,
        caveats=(*STANDING_CAVEATS, *loaded.notes, *exclusion, *skipped),
    )

from __future__ import annotations

import dataclasses
from dataclasses import replace
from datetime import timedelta

from hlsignals.app.config import Settings, SignalSettings, UniverseSettings
from hlsignals.app.wiring import (
    build_market_filter_chain,
    build_signal_engine,
    build_wallet_filter_chain,
    build_wallet_scorer,
)
from hlsignals.backtest.asof import AsOfView, HistoricalData
from hlsignals.backtest.signal_source import HistoricalSignalSource, Scorer, _ViewCandles
from hlsignals.core.clock import MS_PER_DAY, MS_PER_HOUR, from_ms, to_ms
from hlsignals.domain.models import Fill, ScoredWallet, SignalDirection, SignalStatus
from hlsignals.wallets.scoring.scorer import WalletScorer
from hlsignals.wallets.scoring.slice import EquitySlice
from tests.backtest.synthetic import CALENDAR, SESSIONS, SYMBOLS, market
from tests.factories import D, make_fill, make_trips, make_wallet_record, wallet_address

AAA = SYMBOLS[0]
DAY = SESSIONS[100]
AS_OF_MS = to_ms(CALENDAR.sessions(DAY, DAY)[0].open) - 30 * 60_000
WALLETS = [wallet_address(i) for i in (1, 2, 3)]


def history(wallet: str) -> list[Fill]:
    """30 winning 2-day AAA trips ending two days before DAY, then a long still open."""
    trips = make_trips(
        [0.02] * 30,
        hold_ms=2 * MS_PER_DAY,
        gap_ms=MS_PER_DAY // 2,
        symbol=AAA,
        t0_ms=AS_OF_MS - 77 * MS_PER_DAY,
    )
    still_open = make_fill(symbol=AAA, sz=D(3), time_ms=AS_OF_MS - MS_PER_HOUR)
    return [replace(f, wallet=wallet) for f in [*trips, still_open]]


def data(score_as_of_days: float = -10) -> HistoricalData:
    base, _ = market()
    score_date = from_ms(AS_OF_MS) + timedelta(days=score_as_of_days)
    return dataclasses.replace(
        base,
        records=tuple(
            make_wallet_record(address=w, raw_score=90.0, as_of=score_date) for w in WALLETS
        ),
        fills={w: tuple(history(w)) for w in WALLETS},
    )


class CountingScorer:
    def __init__(self, inner: WalletScorer) -> None:
        self.inner = inner
        self.calls = 0

    def score(self, wallet: EquitySlice) -> ScoredWallet:
        self.calls += 1
        return self.inner.score(wallet)


def source(scorer: Scorer | None = None) -> HistoricalSignalSource:
    settings = Settings()
    no_liquidity_floor = UniverseSettings(min_day_volume_usd=0.0, min_open_interest_usd=0.0)
    return HistoricalSignalSource(
        equities=frozenset(SYMBOLS),
        wallet_chain=build_wallet_filter_chain(settings.wallet_filters),
        scorer=scorer or build_wallet_scorer(settings.scoring),
        engine=build_signal_engine(settings.signals),
        market_chain=build_market_filter_chain(no_liquidity_floor),
        calendar=CALENDAR,
        lookback_days=settings.history.lookback_days,
        signal_candle_hours=settings.history.signal_candle_hours,
    )


def view(d: HistoricalData | None = None) -> AsOfView:
    return (d or data()).at(AS_OF_MS)


def test_reconstructed_positions_produce_a_long_signal() -> None:
    signals = {s.symbol: s for s in source().signals_at(view())}
    aaa = signals[AAA]
    assert aaa.status is SignalStatus.SCORED
    assert aaa.direction is SignalDirection.LONG
    assert aaa.n_wallets == 3
    assert aaa.components["tilt"].value == 1.0
    assert all(signals[s].status is SignalStatus.INSUFFICIENT for s in SYMBOLS[1:])


def test_market_context_is_synthesized_from_candles_only() -> None:
    (aaa,) = [i for i in source().inputs_at(view()) if i.symbol == AAA]
    candles = view().candles(AAA)
    assert aaa.market.mark_px == candles[-1].close
    assert aaa.as_of_ms == AS_OF_MS
    assert all(c.close_ms <= AS_OF_MS for c in aaa.candles)


def test_prior_score_taken_after_t_is_not_used() -> None:
    later = source().inputs_at(view(data(score_as_of_days=+1)))[0].wallets
    earlier = source().inputs_at(view(data(score_as_of_days=-1)))[0].wallets
    assert all("prior_score" not in w.features for w in later.values())
    assert all("prior_score" in w.features for w in earlier.values())


def test_other_engines_reuse_the_cached_inputs() -> None:
    counting = CountingScorer(build_wallet_scorer(Settings().scoring))
    base = source(counting)
    base.signals_at(view())
    strict = base.with_engine(build_signal_engine(replace(SignalSettings(), min_wallets=4)))
    (aaa,) = [s for s in strict.signals_at(view()) if s.symbol == AAA]
    assert aaa.status is SignalStatus.INSUFFICIENT  # 3 wallets < 4
    assert counting.calls == 3  # scored once per wallet, not again


def test_symbols_without_candles_by_t_are_left_out() -> None:
    early = data().at(to_ms(CALENDAR.sessions(SESSIONS[0], SESSIONS[0])[0].open) - 60_000)
    assert source().inputs_at(early) == []


def test_view_candles_mapping_is_limited_to_traded_symbols() -> None:
    candles = _ViewCandles(view(), frozenset({AAA}), AS_OF_MS - MS_PER_DAY)
    assert list(candles) == [AAA]
    assert len(candles) == 1
    assert all(c.close_ms <= AS_OF_MS for c in candles[AAA])
    assert candles.get(SYMBOLS[1]) is None

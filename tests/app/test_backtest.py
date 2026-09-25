from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from hlsignals.app.backtest import STANDING_CAVEATS, LoadedHistory, load_history, run_backtest
from hlsignals.app.config import Settings, UniverseSettings
from hlsignals.core.clock import MS_PER_HOUR, to_ms
from hlsignals.core.errors import RetryableError
from hlsignals.domain.models import Candle, DailyBar, Dex, Fill, MarketCtx, Position
from hlsignals.domain.symbols import Symbol
from hlsignals.universe.discovery import EquityDexLocator
from hlsignals.universe.instruments import InstrumentCatalog
from hlsignals.wallets.sources.base import SourceResult
from tests.backtest import test_signal_source as scenario
from tests.backtest.synthetic import CALENDAR, SESSIONS, SYMBOLS, market
from tests.factories import (
    make_candle_series,
    make_market_ctx,
    make_trips,
    make_wallet_record,
    wallet_address,
)

AAA = SYMBOLS[0]


def settings(**backtest: object) -> Settings:
    base = Settings()
    return dataclasses.replace(
        base,
        universe=UniverseSettings(min_day_volume_usd=0.0, min_open_interest_usd=0.0),
        backtest=dataclasses.replace(base.backtest, **backtest),  # type: ignore[arg-type]
    )


def loaded() -> LoadedHistory:
    _, prices = market()
    return LoadedHistory(scenario.data(), prices, ("a load note",))


# --- run_backtest ---------------------------------------------------------------------------------


def test_single_pass_trades_the_reconstructed_signal() -> None:
    start, end = scenario.DAY, SESSIONS[SESSIONS.index(scenario.DAY) + 9]
    report = run_backtest(
        settings=settings(),
        loaded=loaded(),
        calendar=CALENDAR,
        equities=frozenset(SYMBOLS),
        start=start,
        end=end,
        walk_forward=False,
    )
    assert report.single.trades
    assert {t.symbol for t in report.single.trades} == {AAA}
    assert all(t.direction.value == "long" for t in report.single.trades)
    assert report.parameters == "min_trust=0.4 epsilon=0.05"
    assert report.caveats == (*STANDING_CAVEATS, "a load note")
    assert report.walk_forward is None


def test_walk_forward_runs_on_the_grid() -> None:
    start, end = scenario.DAY, SESSIONS[SESSIONS.index(scenario.DAY) + 14]
    report = run_backtest(
        settings=settings(
            train_sessions=8, test_sessions=4, min_trust_grid=(0.3, 0.9), epsilon_grid=(0.05,)
        ),
        loaded=loaded(),
        calendar=CALENDAR,
        equities=frozenset(SYMBOLS),
        start=start,
        end=end,
        walk_forward=True,
    )
    assert report.walk_forward is not None
    assert report.walk_forward.folds
    assert {str(f.chosen) for f in report.walk_forward.folds} <= {
        "min_trust=0.3 epsilon=0.05",
        "min_trust=0.9 epsilon=0.05",
    }
    assert "out-of-sample" in report.verdict() or report.verdict().startswith("INCONCLUSIVE")


def test_walk_forward_without_a_fold_falls_back_to_the_single_pass_and_says_so() -> None:
    """A period too short for one train+test fold must not report an empty 'out-of-sample'
    result: the verdict falls back to the in-sample pass and a caveat explains why."""
    start, end = scenario.DAY, SESSIONS[SESSIONS.index(scenario.DAY) + 9]
    report = run_backtest(
        settings=settings(train_sessions=10, test_sessions=4),
        loaded=loaded(),
        calendar=CALENDAR,
        equities=frozenset(SYMBOLS),
        start=start,
        end=end,
        walk_forward=True,
    )
    assert report.walk_forward is None
    assert report.basis()[2] == "in-sample, configured parameters"
    assert any("walk-forward skipped: 10 sessions, at least 11 needed" in c for c in report.caveats)


def test_prior_scores_dated_after_the_start_are_reported() -> None:
    """A curated score without as_of is dated at load time: hidden from every past session by
    the look-ahead guard, which the report must say instead of silently ignoring it."""
    _, prices = market()
    late = LoadedHistory(scenario.data(score_as_of_days=30), prices, ())
    start = scenario.DAY
    report = run_backtest(
        settings=settings(),
        loaded=late,
        calendar=CALENDAR,
        equities=frozenset(SYMBOLS),
        start=start,
        end=start,
        walk_forward=False,
    )
    assert (
        f"3 wallets' prior scores are dated after {start}: not used in sessions before their "
        "date (a curated entry without as_of is dated when loaded)"
    ) in report.caveats
    on_time = run_backtest(
        settings=settings(),
        loaded=loaded(),
        calendar=CALENDAR,
        equities=frozenset(SYMBOLS),
        start=start,
        end=start,
        walk_forward=False,
    )
    assert not any("prior scores" in c for c in on_time.caveats)


def test_a_period_without_sessions_reports_no_trades_and_no_prior_score_caveat() -> None:
    saturday = next(
        d for d in (scenario.DAY + timedelta(days=i) for i in range(7)) if d.weekday() == 5
    )
    _, prices = market()
    report = run_backtest(
        settings=settings(),
        loaded=LoadedHistory(scenario.data(score_as_of_days=30), prices, ()),
        calendar=CALENDAR,
        equities=frozenset(SYMBOLS),
        start=saturday,
        end=saturday,
        walk_forward=False,
    )
    assert report.single.trades == ()
    assert not any("prior scores" in c for c in report.caveats)


# --- load_history -------------------------------------------------------------------------------

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
START = date(2026, 9, 1)
GOOD, MAKER, BROKEN = (wallet_address(i) for i in (1, 2, 3))
BBB, CCC, LATE, GONE = (Symbol("xyz", c) for c in ("BBB", "CCC", "LATE", "GONE"))


@dataclass
class History:
    fills: list[Fill]
    truncated: bool = False

    def __iter__(self) -> Iterator[Fill]:
        return iter(self.fills)


class Gateway:
    def perp_dexs(self) -> list[Dex]:
        return [Dex("xyz", "XYZ")]

    def meta_and_ctxs(self, dex: str) -> list[MarketCtx]:
        return [make_market_ctx(symbol=s) for s in (AAA, BBB, CCC, LATE)] + [
            make_market_ctx(symbol=GONE, is_delisted=True),
            make_market_ctx(symbol=Symbol("xyz", "GOLD")),
        ]

    def user_fills_by_time(self, user: str, start: datetime, end: datetime | None) -> History:
        if user == BROKEN:
            raise RetryableError("HTTP 503", status=503)
        if user == MAKER:
            return History(
                make_trips(
                    [0.0005] * 1100,
                    hold_ms=10_000,
                    gap_ms=10_000,
                    symbol=AAA,
                    t0_ms=to_ms(start) + 1,
                )
            )
        return History(make_trips([0.01] * 3, symbol=AAA, t0_ms=to_ms(start) + 1))

    def clearinghouse_state(self, user: str, dex: str | None) -> list[Position]:
        raise AssertionError("positions are reconstructed, never fetched in a backtest")

    def candle_snapshot(
        self, symbol: Symbol, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        if symbol == CCC:
            raise RetryableError("HTTP 502", status=502)
        begin = to_ms(start) + (30 * 24 * MS_PER_HOUR if symbol == LATE else 0)
        return make_candle_series([100.0] * 5, symbol=symbol, t0_ms=begin, step_ms=MS_PER_HOUR)


class Prices:
    def daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        if ticker == "BBB":
            raise RetryableError("timeout")
        if ticker == "LATE":
            return []
        return [DailyBar(ticker, start, 10.0, 11.0)]


class Source:
    name = "fixed"

    def fetch(self) -> SourceResult:
        return SourceResult(tuple(make_wallet_record(address=a) for a in (GOOD, MAKER, BROKEN)))


def test_load_history_collects_everything_and_explains_gaps() -> None:
    catalog = InstrumentCatalog.from_mapping(
        {
            "classes": {
                "equity_us": ["xyz:AAA", "xyz:BBB", "xyz:CCC", "xyz:LATE", "xyz:GONE"],
                "commodity": ["xyz:GOLD"],
            }
        }
    )
    equities = catalog.symbols_in(frozenset({"equity_us"}))
    gateway = Gateway()
    result = load_history(
        settings=settings(),
        gateway=gateway,
        locator=EquityDexLocator(gateway, catalog, frozenset({"equity_us"}), ("xyz",)),
        wallet_source=Source(),
        equities=equities,
        prices=Prices(),
        start=START,
        now=NOW,
    )
    assert set(result.data.markets) == {AAA, BBB, CCC, LATE}  # delisted and GOLD excluded
    assert [r.address for r in result.data.records] == [GOOD]
    assert set(result.data.candles) == {AAA, BBB, LATE}
    assert result.prices.open_on("AAA", START) == 10.0
    notes = " | ".join(result.notes)
    for needle in (
        "1 wallets dropped by the market-maker prescreen",
        "1 wallets skipped: fills could not be fetched",
        "no candles for xyz:CCC",
        "starts after the requested start",
        "xyz:LATE",
        "no stock bars for BBB",
        "no stock bars returned for: LATE",
    ):
        assert needle in notes


def test_excluded_wallets_are_reported_in_the_caveats() -> None:
    broken = wallet_address(9)
    base = loaded()
    bad = dataclasses.replace(scenario.history(broken)[0], dir="Mystery Direction")
    data = dataclasses.replace(
        base.data,
        records=(*base.data.records, make_wallet_record(address=broken)),
        fills={**base.data.fills, broken: (bad,)},
    )
    start = scenario.DAY
    report = run_backtest(
        settings=settings(),
        loaded=LoadedHistory(data, base.prices, ()),
        calendar=CALENDAR,
        equities=frozenset(SYMBOLS),
        start=start,
        end=start,
        walk_forward=False,
    )
    assert report.single.trades  # the good wallets still trade
    assert any("1 wallets left out" in c and "Mystery Direction" in c for c in report.caveats)

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

import pytest

from hlsignals.app.candles import CandleCache
from hlsignals.app.config import HistorySettings, ScoringSettings, WalletFilterSettings
from hlsignals.app.vet import Prescreen, VetResult, WalletVetter, render_vet
from hlsignals.app.wiring import build_wallet_filter_chain, build_wallet_scorer
from hlsignals.core.clock import MS_PER_DAY, from_ms, to_ms
from hlsignals.core.errors import RetryableError
from hlsignals.domain.models import Candle, Fill
from hlsignals.domain.symbols import Symbol
from tests.factories import (
    AAPL,
    BTC,
    NVDA,
    OTHER_WALLET,
    T0_MS,
    WALLET,
    make_candle_series,
    make_trips,
    make_wallet_record,
)

NOW = from_ms(T0_MS) + timedelta(days=40)
EQUITIES = frozenset({NVDA, AAPL})


@dataclass
class History:
    fills: list[Fill]
    truncated: bool = False
    consumed: int = 0

    def __iter__(self) -> Iterator[Fill]:
        for fill in self.fills:
            self.consumed += 1
            yield fill


@dataclass
class FakeGateway:
    fills: dict[str, list[Fill] | Exception]
    fill_requests: list[tuple[str, datetime, datetime | None]] = field(default_factory=list)
    candle_requests: list[Symbol] = field(default_factory=list)
    histories: list[History] = field(default_factory=list)

    def user_fills_by_time(self, user: str, start: datetime, end: datetime | None) -> History:
        self.fill_requests.append((user, start, end))
        result = self.fills[user]
        if isinstance(result, Exception):
            raise result
        history = History(result, truncated=len(result) > 100)
        self.histories.append(history)
        return history

    def candle_snapshot(
        self, symbol: Symbol, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        self.candle_requests.append(symbol)
        return make_candle_series([100.0] * 3, symbol=symbol)


def swing_fills(wallet: str, n: int = 12) -> list[Fill]:
    trips = make_trips([0.02, -0.01, 0.03] * (n // 3), hold_ms=2 * MS_PER_DAY)
    return [replace(f, wallet=wallet) for f in trips]


def vetter(gateway: FakeGateway, prescreen: Prescreen | None = None) -> WalletVetter:
    return WalletVetter(
        fills=gateway,
        candles=CandleCache(gateway, "1h"),
        equities=EQUITIES,
        chain=build_wallet_filter_chain(WalletFilterSettings()),
        scorer=build_wallet_scorer(ScoringSettings()),
        settings=HistorySettings(lookback_days=90.0),
        prescreen=prescreen,
    )


def test_vet_scores_accepted_wallet_and_fetches_candles_for_traded_equities() -> None:
    fills = swing_fills(WALLET) + [f for f in make_trips([0.5], symbol=BTC)]
    gateway = FakeGateway({WALLET: sorted(fills, key=lambda f: f.time_ms)})
    (result,) = vetter(gateway).vet_all([make_wallet_record()], NOW)
    assert result.rejection is None
    assert result.error is None
    assert result.scored is not None
    assert result.scored.trust > 0
    assert result.n_trips == 12
    assert gateway.candle_requests == []  # multi-day holds: the bait filter needs no prices
    user, start, end = gateway.fill_requests[0]
    assert (user, end) == (WALLET, NOW)
    assert NOW - start == timedelta(days=90)


def test_rejected_wallet_still_scored_with_reason() -> None:
    gateway = FakeGateway({WALLET: swing_fills(WALLET, n=3)})
    (result,) = vetter(gateway).vet_all([make_wallet_record()], NOW)
    assert result.rejection == ("min_sample", "3 scored round trips < 10")
    assert result.scored is not None


def test_api_error_on_one_wallet_does_not_stop_the_rest() -> None:
    gateway = FakeGateway(
        {WALLET: RetryableError("down", status=503), OTHER_WALLET: swing_fills(OTHER_WALLET)}
    )
    results = vetter(gateway).vet_all(
        [make_wallet_record(), make_wallet_record(address=OTHER_WALLET)], NOW
    )
    assert [r.record.address for r in results] == [OTHER_WALLET, WALLET]  # errors last
    assert results[1].error is not None
    assert "down" in results[1].error


def test_ordering_accepted_by_trust_then_rejected() -> None:
    strong, weak = "0x" + "11" * 20, "0x" + "22" * 20
    gateway = FakeGateway(
        {
            strong: swing_fills(strong, n=30),
            weak: swing_fills(weak, n=12),
            WALLET: swing_fills(WALLET, n=3),
        }
    )
    records = [make_wallet_record(address=a) for a in (WALLET, weak, strong)]
    assert [r.record.address for r in vetter(gateway).vet_all(records, NOW)] == [
        strong,
        weak,
        WALLET,
    ]


def test_truncated_history_is_flagged() -> None:
    gateway = FakeGateway({WALLET: swing_fills(WALLET, n=102)})
    (result,) = vetter(gateway).vet_all([make_wallet_record()], NOW)
    assert result.truncated


def test_render_explains_everything() -> None:
    gateway = FakeGateway(
        {WALLET: swing_fills(WALLET), OTHER_WALLET: swing_fills(OTHER_WALLET, n=3)}
    )
    text = render_vet(
        vetter(gateway).vet_all(
            [make_wallet_record(), make_wallet_record(address=OTHER_WALLET)], NOW
        )
    )
    assert WALLET in text
    assert OTHER_WALLET in text
    assert "ACCEPTED" in text
    assert "REJECTED min_sample: 3 scored round trips < 10" in text
    for name in (
        "hit_rate",
        "drawdown",
        "consistency",
        "horizon_fit",
        "trust",
        "confidence",
        "decay",
    ):
        assert name in text
    assert "wins=" in text  # evidence is shown


def test_render_error_and_empty() -> None:
    result = VetResult(make_wallet_record(), None, None, "RetryableError: down")
    assert "ERROR RetryableError: down" in render_vet([result])
    assert render_vet([]) == "no wallets to vet\n"


def test_render_rejection_without_score() -> None:
    result = VetResult(
        make_wallet_record(), None, ("min_sample", "0 scored round trips < 10"), None
    )
    assert "REJECTED min_sample" in render_vet([result])


def test_history_kept_only_for_accepted_wallets() -> None:
    gateway = FakeGateway(
        {WALLET: swing_fills(WALLET), OTHER_WALLET: swing_fills(OTHER_WALLET, n=3)}
    )
    accepted, rejected = vetter(gateway).vet_all(
        [make_wallet_record(), make_wallet_record(address=OTHER_WALLET)], NOW
    )
    assert accepted.wallet is not None
    assert rejected.wallet is None
    assert rejected.n_fills == 6  # counts survive for the report


def test_candles_fetched_only_for_short_trips_the_bait_filter_checks() -> None:
    recent = to_ms(NOW) - 26 * MS_PER_DAY  # ends about a day before NOW: passes inactivity
    quick = make_trips([0.01] * 12, hold_ms=2 * 3_600_000, gap_ms=2 * MS_PER_DAY, t0_ms=recent)
    btc = make_trips([0.01] * 12, hold_ms=2 * 3_600_000, symbol=BTC, t0_ms=recent)
    gateway = FakeGateway({WALLET: sorted([*quick, *btc], key=lambda f: f.time_ms)})
    vetter(gateway).vet_all([make_wallet_record()], NOW)
    assert gateway.candle_requests == [NVDA]  # crypto never, and each symbol once


def maker_fills(n_trips: int) -> list[Fill]:
    fills = make_trips([0.0005, -0.0005] * (n_trips // 2), hold_ms=30_000, gap_ms=30_000)
    return [replace(f, time_ms=f.time_ms + to_ms(NOW) - 2 * MS_PER_DAY) for f in fills]


def test_prescreen_rejects_heavy_maker_without_reading_the_rest() -> None:
    gateway = FakeGateway({WALLET: maker_fills(120)})  # 240 fills within an hour
    (result,) = vetter(gateway, Prescreen(page_size=100, max_fills_per_day=50.0)).vet_all(
        [make_wallet_record()], NOW
    )
    assert result.rejection is not None
    assert result.rejection[0] == "maker_profile"
    assert result.rejection[1].startswith("prescreen: 100 US-stock fills")
    assert result.scored is None
    assert result.n_fills == 100
    assert gateway.histories[0].consumed == 100


def test_prescreen_passes_slow_full_page_and_reads_everything() -> None:
    gateway = FakeGateway({WALLET: swing_fills(WALLET, n=12)})  # 24 fills over weeks
    (result,) = vetter(gateway, Prescreen(page_size=24, max_fills_per_day=50.0)).vet_all(
        [make_wallet_record()], NOW
    )
    assert result.scored is not None
    assert gateway.histories[0].consumed == 24


def test_prescreen_skipped_for_partial_first_page() -> None:
    gateway = FakeGateway({WALLET: maker_fills(10)})  # 20 fills < page of 100
    (result,) = vetter(gateway, Prescreen(page_size=100, max_fills_per_day=1.0)).vet_all(
        [make_wallet_record()], NOW
    )
    # Not stopped by the prescreen: every fill is read and the full chain judges the wallet.
    assert gateway.histories[0].consumed == 20
    assert result.rejection is not None
    assert result.rejection[0] == "min_sample"  # the chain's first filter; 10 trips are too few
    assert not result.rejection[1].startswith("prescreen")


def test_progress_logged_every_ten_wallets(caplog: pytest.LogCaptureFixture) -> None:
    addresses = [f"0x{i:040x}" for i in range(1, 11)]
    gateway = FakeGateway({a: swing_fills(a, n=3) for a in addresses})
    with caplog.at_level(logging.INFO):
        vetter(gateway).vet_all([make_wallet_record(address=a) for a in addresses], NOW)
    assert "vetted 10 wallets" in caplog.text

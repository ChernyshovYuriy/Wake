"""A synthetic exchange for pipeline tests: every branch of the pipeline in one run."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from hlsignals.core.clock import MS_PER_DAY, MS_PER_HOUR, to_ms
from hlsignals.core.errors import RetryableError
from hlsignals.domain.models import Candle, Dex, Fill, MarketCtx, Position, WalletRecord
from hlsignals.domain.symbols import Symbol
from hlsignals.wallets.sources.base import SourceResult
from tests.factories import (
    D,
    make_candle_series,
    make_fill,
    make_market_ctx,
    make_position,
    make_trips,
    make_wallet_record,
    wallet_address,
)

# Thursday before the US open: last cash close was Wednesday 16:00 ET (20:00 UTC).
AS_OF = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
AS_OF_MS = to_ms(AS_OF)
NVDA, AAPL, TSLA, MU = (Symbol("xyz", c) for c in ("NVDA", "AAPL", "TSLA", "MU"))
SWING = [wallet_address(i) for i in (1, 2, 3, 4)]
MAKER, BROKEN_FILLS, BROKEN_POSITIONS = (wallet_address(i) for i in (5, 6, 7))


def swing_history(wallet: str, symbol: Symbol) -> list[Fill]:
    """30 winning 2-day trips ending a day before AS_OF, then one buy 2 hours ago."""
    trips = make_trips(
        [0.02] * 30,
        hold_ms=2 * MS_PER_DAY,
        gap_ms=MS_PER_DAY // 2,
        symbol=symbol,
        t0_ms=AS_OF_MS - 76 * MS_PER_DAY,
    )
    recent = make_fill(symbol=symbol, sz=D(500), px=D(100), time_ms=AS_OF_MS - 2 * MS_PER_HOUR)
    return [replace(f, wallet=wallet) for f in [*trips, recent]]


def maker_history() -> list[Fill]:
    fills = make_trips(
        [0.0005, -0.0005] * 60,
        hold_ms=30_000,
        gap_ms=60_000,
        crossed=False,
        t0_ms=AS_OF_MS - 3 * MS_PER_HOUR,
    )
    return [replace(f, wallet=MAKER) for f in fills]


def market(symbol: Symbol, volume: float = 50_000_000.0) -> MarketCtx:
    return make_market_ctx(symbol=symbol, mark_px=100.0, open_interest=20_000.0, day_ntl_vlm=volume)


@dataclass
class History:
    fills: list[Fill]
    truncated: bool = False

    def __iter__(self) -> Iterator[Fill]:
        return iter(self.fills)


@dataclass
class ScenarioGateway:
    candle_calls: list[Symbol] = field(default_factory=list)

    def perp_dexs(self) -> list[Dex]:
        return [Dex("xyz", "XYZ"), Dex("km", "Kinetiq")]

    def meta_and_ctxs(self, dex: str) -> list[MarketCtx]:
        assert dex == "xyz", "km lists nothing we include and must not be queried"
        return [
            market(NVDA),
            market(AAPL),
            market(TSLA),
            market(MU, volume=500_000.0),
            market(Symbol("xyz", "GOLD")),  # commodity: excluded by class
            market(Symbol("xyz", "NEWCO")),  # not in the catalog: unclassified
        ]

    def user_fills_by_time(self, user: str, start: datetime, end: datetime | None) -> History:
        assert end == AS_OF
        if user == BROKEN_FILLS:
            raise RetryableError("HTTP 503", status=503)
        if user == MAKER:
            return History(maker_history())
        symbol = AAPL if user == SWING[3] else NVDA
        return History(swing_history(user, symbol))

    def clearinghouse_state(self, user: str, dex: str | None) -> list[Position]:
        if user == BROKEN_POSITIONS:
            raise RetryableError("timeout")
        if user == SWING[3]:
            return [make_position(wallet=user, symbol=AAPL, size=D(-10))]
        return [
            make_position(wallet=user, symbol=NVDA, size=D(20)),
            make_position(wallet=user, symbol=AAPL, size=D(5))
            if user == SWING[0]
            else make_position(wallet=user, symbol=MU, size=D(1)),
        ]

    def candle_snapshot(
        self, symbol: Symbol, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        self.candle_calls.append(symbol)
        if symbol == TSLA:
            raise RetryableError("HTTP 502", status=502)
        hours = int((end - start) / timedelta(hours=1))
        drift = 0.02 if symbol == NVDA else 0.0
        return make_candle_series(
            [100.0 + drift * i for i in range(hours)],
            symbol=symbol,
            t0_ms=to_ms(start),
            step_ms=MS_PER_HOUR,
        )


def records() -> list[WalletRecord]:
    everyone = [*SWING, MAKER, BROKEN_FILLS, BROKEN_POSITIONS]
    return [make_wallet_record(address=a, as_of=AS_OF) for a in everyone]


class FixedSource:
    name = "fixed"

    def fetch(self) -> SourceResult:
        return SourceResult(tuple(records()), (), used=("fixed",))

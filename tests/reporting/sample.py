"""A fixed, representative report used by the renderer snapshot and contract tests."""

from __future__ import annotations

from datetime import UTC, datetime

from hlsignals.domain.models import (
    FeatureValue,
    SignalComponent,
    SignalDirection,
    SignalFlag,
    SignalReport,
    SignalStatus,
    TickerSignal,
)
from hlsignals.domain.symbols import Symbol
from tests.factories import make_diagnostics, make_market_ctx, make_scored_wallet, wallet_address

AS_OF = datetime(2026, 9, 24, 12, 45, tzinfo=UTC)


def components(tilt: float, flow: float, overnight: float) -> dict[str, SignalComponent]:
    return {
        "tilt": SignalComponent(
            tilt, {"net_usd": tilt * 1e6, "gross_usd": 1e6, "n_long": 3, "n_short": 1}
        ),
        "flow": SignalComponent(flow, {"flow_usd": flow * 2e5, "oi_usd": 2e6, "n_fills": 7}),
        "overnight": SignalComponent(
            overnight,
            {
                "ref_px": 100.0,
                "last_px": 100.0 + overnight * 3,
                "pct": overnight * 0.03,
                "stale": False,
            },
        ),
    }


def signal(
    coin: str,
    score: float | None,
    direction: SignalDirection | None,
    parts: tuple[float, float, float],
    *,
    flags: frozenset[SignalFlag] = frozenset(),
    n_wallets: int = 4,
) -> TickerSignal:
    symbol = Symbol("xyz", coin)
    scored = score is not None
    return TickerSignal(
        symbol=symbol,
        status=SignalStatus.SCORED if scored else SignalStatus.INSUFFICIENT,
        direction=direction,
        score=score,
        components=components(*parts),
        n_wallets=n_wallets,
        reason=f"{direction.value}: sample"
        if direction
        else "2 trusted wallets < 3 (trust >= 0.4)",
        flags=flags,
        market=make_market_ctx(
            symbol=symbol, mark_px=200.0, open_interest=10_000.0, day_ntl_vlm=4.2e7
        ),
    )


def sample_report() -> SignalReport:
    signals = (
        signal("NVDA", 0.62, SignalDirection.LONG, (0.8, 0.6, 0.3)),
        signal(
            "TSLA",
            -0.41,
            SignalDirection.SHORT,
            (-0.5, -0.4, -0.2),
            flags=frozenset({SignalFlag.THIN_VOLUME}),
        ),
        signal(
            "AAPL",
            0.03,
            SignalDirection.FLAT,
            (0.1, 0.0, -0.1),
            flags=frozenset({SignalFlag.CASH_SESSION_OPEN, SignalFlag.WEAK_SAMPLE}),
        ),
        signal("META", None, None, (0.2, 0.0, 0.0), n_wallets=2),
    )
    wallets = (
        make_scored_wallet(
            address=wallet_address(1),
            trust=0.61,
            confidence=0.75,
            decay=0.97,
            features={"hit_rate": FeatureValue(0.7, {"wins": 21, "n_trips": 30})},
            n_closed_lots=30,
            track_record_days=74.5,
        ),
        make_scored_wallet(address=wallet_address(2), trust=0.44),
    )
    return SignalReport(AS_OF, signals, make_diagnostics(), wallets)


def empty_report() -> SignalReport:
    return SignalReport(AS_OF, (), make_diagnostics(wallets_accepted=0, universe_after_filters=0))


def insufficient_report() -> SignalReport:
    return SignalReport(
        AS_OF, (signal("META", None, None, (0.0, 0.0, 0.0), n_wallets=1),), make_diagnostics()
    )

"""End-to-end pipeline on a synthetic exchange: deterministic, every branch exercised.
Regenerate the snapshot after an intended change: UPDATE_SNAPSHOTS=1 pytest tests/app"""

from __future__ import annotations

import dataclasses
import logging
import os
from pathlib import Path

import pytest

from hlsignals.app.config import Settings
from hlsignals.app.pipeline import PipelineParts, SignalPipeline
from hlsignals.app.wiring import (
    build_market_filter_chain,
    build_signal_engine,
    build_wallet_filter_chain,
    build_wallet_scorer,
    load_calendar,
    load_catalog,
    load_equities,
)
from hlsignals.domain.models import SignalDirection, SignalReport, SignalStatus
from hlsignals.reporting.renderers import JsonRenderer
from hlsignals.universe.discovery import EquityDexLocator
from tests.app.scenario import (
    AAPL,
    AS_OF,
    BROKEN_POSITIONS,
    MU,
    NVDA,
    TSLA,
    FixedSource,
    ScenarioGateway,
)

ROOT = Path(__file__).parents[2]
SNAPSHOT = Path(__file__).parent / "snapshots" / "pipeline.json"


def pipeline(gateway: ScenarioGateway, settings: Settings | None = None) -> SignalPipeline:
    settings = settings or Settings()
    catalog = load_catalog(ROOT / settings.universe.instruments_path)
    return SignalPipeline(
        PipelineParts(
            settings=settings,
            gateway=gateway,
            calendar=load_calendar(ROOT / settings.calendar.path),
            locator=EquityDexLocator(
                gateway,
                catalog,
                settings.universe.include_classes,
                settings.universe.dex_preference,
            ),
            market_chain=build_market_filter_chain(settings.universe),
            wallet_source=FixedSource(),
            equities=load_equities(catalog, settings.universe.include_classes),
            wallet_chain=build_wallet_filter_chain(settings.wallet_filters),
            scorer=build_wallet_scorer(settings.scoring),
            engine=build_signal_engine(settings.signals),
        )
    )


def report() -> SignalReport:
    return pipeline(ScenarioGateway()).run(AS_OF)


def test_snapshot_is_deterministic() -> None:
    rendered = JsonRenderer().render(report())
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_text(rendered)
    assert rendered == SNAPSHOT.read_text()
    assert JsonRenderer().render(report()) == rendered


def test_signals() -> None:
    signals = {s.symbol: s for s in report().signals}
    assert set(signals) == {NVDA, AAPL, TSLA}  # MU filtered for volume
    nvda = signals[NVDA]
    assert nvda.status is SignalStatus.SCORED
    assert nvda.direction is SignalDirection.LONG
    assert nvda.n_wallets == 3
    assert signals[AAPL].status is SignalStatus.INSUFFICIENT  # only 2 trusted wallets
    assert signals[TSLA].status is SignalStatus.INSUFFICIENT
    assert report().signals[0].symbol == NVDA  # ranked first


def test_diagnostics_explain_every_exclusion() -> None:
    d = report().diagnostics
    assert d.sources_used == ("fixed",)
    assert d.wallets_considered == 7
    assert d.wallets_accepted == 4
    assert d.wallets_rejected == {"maker_profile": 1, "api_error": 1, "positions_error": 1}
    assert d.universe_discovered == 4
    assert d.universe_after_filters == 3
    assert d.markets_rejected == {"min_day_volume": 1}
    assert d.unclassified_symbols == ("xyz:NEWCO",)
    assert any(BROKEN_POSITIONS in note for note in d.notes)
    assert any("candles unavailable for xyz:TSLA" in note for note in d.notes)


def test_positions_outside_the_universe_are_ignored() -> None:
    nvda = next(s for s in report().signals if s.symbol == NVDA)
    assert nvda.components["tilt"].evidence["n_long"] == 3
    assert all(s.symbol != MU for s in report().signals)


def test_candles_fetched_once_per_symbol() -> None:
    gateway = ScenarioGateway()
    pipeline(gateway).run(AS_OF)
    assert sorted(map(str, gateway.candle_calls)) == sorted(map(str, {NVDA, AAPL, TSLA}))


def test_progress_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        report()
    text = caplog.text
    assert "universe: 4 markets discovered, 3 after filters" in text
    assert "vetting 7 wallets" in text
    assert "computing signals for 3 markets" in text


def test_no_maker_prescreen_without_the_maker_filter() -> None:
    settings = Settings()
    order = tuple(n for n in settings.wallet_filters.order if n != "maker_profile")
    settings = dataclasses.replace(
        settings, wallet_filters=dataclasses.replace(settings.wallet_filters, order=order)
    )
    rejected = pipeline(ScenarioGateway(), settings).run(AS_OF).diagnostics.wallets_rejected
    assert "maker_profile" not in rejected

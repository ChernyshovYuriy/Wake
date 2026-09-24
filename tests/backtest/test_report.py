from __future__ import annotations

import json

import pytest

from hlsignals.backtest.metrics import compute_metrics
from hlsignals.backtest.replay import ReplayResult, Trade
from hlsignals.backtest.report import BacktestReport, backtest_to_json, render_backtest
from hlsignals.backtest.walkforward import Fold, FoldResult, WalkForwardResult
from hlsignals.domain.models import SignalDirection
from tests.backtest.synthetic import SESSIONS, SYMBOLS

DAYS = tuple(SESSIONS[:40])


def trades(n: int, net: float, bench: float) -> tuple[Trade, ...]:
    return tuple(
        Trade(
            DAYS[i % 40],
            DAYS[i % 40],
            SYMBOLS[0],
            "AAA",
            SignalDirection.LONG,
            0.5,
            100.0,
            101.0,
            net,
            net,
            bench,
        )
        for i in range(n)
    )


def report(
    n: int, net: float = 0.01, bench: float = 0.002, walk_forward: bool = False
) -> BacktestReport:
    single = ReplayResult(DAYS, trades(n, net, bench), {"missing stock bars": 2}, 5)
    wf = None
    if walk_forward:
        fold = Fold(DAYS[:20], DAYS[20:])
        test = ReplayResult(DAYS[20:], trades(n, net, bench), {}, 5)
        metrics = compute_metrics([net] * n, horizon_days=5, sessions=20, exposed_sessions=20)
        bench_m = compute_metrics([bench] * n, horizon_days=5, sessions=20, exposed_sessions=20)
        wf = WalkForwardResult(
            (FoldResult(fold, "min_trust=0.3 epsilon=0.05", metrics, test),), metrics, bench_m
        )
    return BacktestReport(
        start=DAYS[0],
        end=DAYS[-1],
        horizon_sessions=5,
        cost_bps_per_side=5.0,
        min_trades_for_verdict=30,
        parameters="min_trust=0.4 epsilon=0.05",
        single=single,
        walk_forward=wf,
        caveats=("open interest is approximated",),
    )


def test_small_sample_is_inconclusive() -> None:
    assert report(12).verdict().startswith("INCONCLUSIVE: 12 trades < 30")


def test_beat_and_did_not_beat() -> None:
    assert report(40).verdict().startswith("BEAT buy-and-hold by +0.80% per trade (in-sample")
    assert (
        report(40, net=0.0)
        .verdict()
        .startswith("DID NOT BEAT buy-and-hold (-0.20% per trade, in-sample")
    )


def test_walk_forward_is_the_basis_when_present() -> None:
    assert "out-of-sample" in report(40, walk_forward=True).verdict()


def test_text_shows_sample_size_benchmark_regime_and_caveats() -> None:
    text = render_backtest(report(40, walk_forward=True))
    for needle in (
        f"{DAYS[0]} .. {DAYS[-1]}",
        "40 sessions",
        "horizon 5 sessions",
        "5 bps/side",
        "strategy",
        "buy-and-hold",
        "trades",
        "Walk-forward",
        "chosen min_trust=0.3",
        "missing stock bars 2",
        "open interest is approximated",
        "Verdict:",
    ):
        assert needle in text


def test_json_is_complete() -> None:
    doc = json.loads(backtest_to_json(report(40, walk_forward=True)))
    assert doc["verdict"].startswith("BEAT")
    assert doc["sample"]["trades"] == 40
    assert doc["strategy"]["mean"] == pytest.approx(0.01)
    assert doc["benchmark"]["mean"] == pytest.approx(0.002)
    fold = doc["walk_forward"]["folds"][0]
    assert fold["chosen"] == "min_trust=0.3 epsilon=0.05"
    assert fold["train"] == [str(DAYS[0]), str(DAYS[19])]
    assert fold["train_metrics"]["n_trades"] == 40
    assert doc["caveats"] == ["open interest is approximated"]


def test_zero_trades_render() -> None:
    text = render_backtest(report(0))
    assert "INCONCLUSIVE: 0 trades" in text

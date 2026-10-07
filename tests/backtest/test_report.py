from __future__ import annotations

import json

import pytest

from hlsignals.backtest.metrics import Metrics, compute_metrics
from hlsignals.backtest.replay import ReplayResult, Trade
from hlsignals.backtest.report import BacktestReport, backtest_to_json, render_backtest
from hlsignals.backtest.walkforward import Fold, FoldResult, WalkForwardResult
from hlsignals.domain.models import SignalDirection
from tests.backtest.synthetic import SESSIONS, SYMBOLS

DAYS = tuple(SESSIONS[:40])


def trades(n: int, net: float, bench: float, basket: float) -> tuple[Trade, ...]:
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
            basket,
        )
        for i in range(n)
    )


def report(
    n: int,
    net: float = 0.01,
    bench: float = 0.002,
    walk_forward: bool = False,
    basket: float = 0.015,
) -> BacktestReport:
    single = ReplayResult(DAYS, trades(n, net, bench, basket), {"missing stock bars": 2}, 5)
    wf = None
    if walk_forward:
        fold = Fold(DAYS[:20], DAYS[20:])
        test = ReplayResult(DAYS[20:], trades(n, net, bench, basket), {}, 5)

        def metrics(r: float) -> Metrics:
            return compute_metrics([r] * n, horizon_days=5, sessions=20, exposed_sessions=20)

        wf = WalkForwardResult(
            (FoldResult(fold, "min_trust=0.3 epsilon=0.05", metrics(net), test),),
            metrics(net),
            metrics(bench),
            metrics(basket),
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
    assert "out-of-sample" in report(40, walk_forward=True).basket_verdict()


def test_basket_verdict_is_judged_against_the_basket() -> None:
    # +1.00% per trade beats same-stock buy-and-hold (+0.20%) but not the basket (+1.50%).
    assert (
        report(40)
        .basket_verdict()
        .startswith("DID NOT BEAT the basket (-0.50% per trade, in-sample")
    )
    assert (
        report(40, basket=0.004)
        .basket_verdict()
        .startswith("BEAT the basket by +0.60% per trade (in-sample")
    )
    assert report(12).basket_verdict().startswith("INCONCLUSIVE: 12 trades < 30")


def test_text_shows_sample_size_benchmark_regime_and_caveats() -> None:
    text = render_backtest(report(40, walk_forward=True))
    for needle in (
        f"{DAYS[0]} .. {DAYS[-1]}",
        "40 sessions",
        "horizon 5 sessions",
        "5 bps/side",
        "strategy",
        "buy-and-hold",
        "basket",
        "Vs basket: DID NOT BEAT the basket",
        "the equal-weight basket +1.50%",
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
    assert doc["basket"]["mean"] == pytest.approx(0.015)
    assert doc["basket_verdict"].startswith("DID NOT BEAT the basket")
    fold = doc["walk_forward"]["folds"][0]
    assert fold["chosen"] == "min_trust=0.3 epsilon=0.05"
    assert fold["train"] == [str(DAYS[0]), str(DAYS[19])]
    assert fold["train_metrics"]["n_trades"] == 40
    assert fold["test_basket"]["mean"] == pytest.approx(0.015)
    assert doc["caveats"] == ["open interest is approximated"]


def test_zero_trades_render() -> None:
    text = render_backtest(report(0))
    assert "INCONCLUSIVE: 0 trades" in text

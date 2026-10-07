"""Backtest report: sample size, period and regime, strategy vs buy-and-hold and vs the
equal-weight basket, a verdict against each, walk-forward folds, skipped trades and caveats.
Below ``min_trades_for_verdict`` both verdicts are INCONCLUSIVE, whatever the numbers say."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from hlsignals.backtest.metrics import Metrics
from hlsignals.backtest.replay import ReplayResult
from hlsignals.backtest.walkforward import WalkForwardResult


@dataclass(frozen=True, slots=True)
class Basis:
    """The results a verdict is based on: walk-forward out-of-sample, else the single pass."""

    strategy: Metrics
    benchmark: Metrics  # buy-and-hold of each traded stock
    basket: Metrics  # equal-weight basket of every priced stock
    label: str


@dataclass(frozen=True, slots=True)
class BacktestReport:
    start: date
    end: date
    horizon_sessions: int
    cost_bps_per_side: float
    min_trades_for_verdict: int
    parameters: str  # the configured signal parameters of the single pass
    single: ReplayResult
    walk_forward: WalkForwardResult[Any] | None
    caveats: tuple[str, ...]

    def basis(self) -> Basis:
        if self.walk_forward is not None:
            wf = self.walk_forward
            return Basis(wf.out_of_sample, wf.benchmark, wf.basket, "out-of-sample walk-forward")
        return Basis(
            self.single.metrics(),
            self.single.benchmark_metrics(),
            self.single.basket_metrics(),
            "in-sample, configured parameters",
        )

    def verdict(self) -> str:
        """Against buy-and-hold of the same stocks: a long ties it, only correct shorts win."""
        basis = self.basis()
        return self._verdict(basis.strategy, basis.benchmark, "buy-and-hold", basis.label)

    def basket_verdict(self) -> str:
        """Against the equal-weight basket: credits picking stocks, long or short."""
        basis = self.basis()
        return self._verdict(basis.strategy, basis.basket, "the basket", basis.label)

    def _verdict(self, strategy: Metrics, benchmark: Metrics, name: str, label: str) -> str:
        n = strategy.n_trades
        if n < self.min_trades_for_verdict or strategy.mean is None or benchmark.mean is None:
            return f"INCONCLUSIVE: {n} trades < {self.min_trades_for_verdict} needed for a verdict"
        edge = strategy.mean - benchmark.mean
        if edge > 0:
            return f"BEAT {name} by {edge:+.2%} per trade ({label}, {n} trades)"
        return f"DID NOT BEAT {name} ({edge:+.2%} per trade, {label}, {n} trades)"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:+.2%}"


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _row(name: str, m: Metrics) -> str:
    hit = "-" if m.hit_rate is None else f"{m.hit_rate:.0%}"
    return (
        f"  {name:<13} {m.n_trades:>6}  {hit:>5}  {_pct(m.mean):>8}  {_pct(m.median):>8}"
        f"  {_num(m.sharpe):>6}  {m.max_drawdown:>7.2%}  {m.exposure:>8.0%}"
    )


def render_backtest(report: BacktestReport) -> str:
    basis = report.basis()
    single = report.single
    out = [
        f"Backtest {report.start} .. {report.end} ({len(single.sessions)} sessions), "
        f"horizon {report.horizon_sessions} sessions, cost {report.cost_bps_per_side:g} bps/side",
        f"Configured parameters: {report.parameters}",
        f"Verdict: {report.verdict()}",
        f"Vs basket: {report.basket_verdict()}",
        "",
        f"Results ({basis.label}):",
        f"  {'':<13} {'trades':>6}  {'hit':>5}  {'mean':>8}  {'median':>8}"
        f"  {'sharpe':>6}  {'max DD':>7}  {'exposure':>8}",
        _row("strategy", basis.strategy),
        _row("buy-and-hold", basis.benchmark),
        _row("basket", basis.basket),
        f"  regime: buy-and-hold of the traded names averaged {_pct(basis.benchmark.mean)}, "
        f"the equal-weight basket {_pct(basis.basket.mean)}, per "
        f"{report.horizon_sessions}-session window",
    ]
    if report.walk_forward is not None:
        out += ["", "Walk-forward folds (parameters chosen on train, applied to test):"]
        for fold in report.walk_forward.folds:
            test = fold.test.metrics()
            out.append(
                f"  train {fold.fold.train[0]}..{fold.fold.train[-1]} -> test "
                f"{fold.fold.test[0]}..{fold.fold.test[-1]}: chosen {fold.chosen}; "
                f"train mean {_pct(fold.train_metrics.mean)} ({fold.train_metrics.n_trades}), "
                f"test mean {_pct(test.mean)} ({test.n_trades}) vs buy-and-hold "
                f"{_pct(fold.test.benchmark_metrics().mean)}, basket "
                f"{_pct(fold.test.basket_metrics().mean)}"
            )
    skipped = ", ".join(f"{k} {v}" for k, v in sorted(single.skipped.items())) or "none"
    out += ["", f"Skipped signals: {skipped}", "", "Caveats:"]
    out += [f"  - {c}" for c in report.caveats]
    return "\n".join(out) + "\n"


def backtest_to_json(report: BacktestReport) -> str:
    basis = report.basis()
    doc: dict[str, Any] = {
        "period": {"start": str(report.start), "end": str(report.end)},
        "sample": {"sessions": len(report.single.sessions), "trades": basis.strategy.n_trades},
        "horizon_sessions": report.horizon_sessions,
        "cost_bps_per_side": report.cost_bps_per_side,
        "parameters": report.parameters,
        "basis": basis.label,
        "verdict": report.verdict(),
        "basket_verdict": report.basket_verdict(),
        "strategy": asdict(basis.strategy),
        "benchmark": asdict(basis.benchmark),
        "basket": asdict(basis.basket),
        "skipped": dict(report.single.skipped),
        "caveats": list(report.caveats),
        "walk_forward": None,
    }
    if report.walk_forward is not None:
        doc["walk_forward"] = {
            "folds": [
                {
                    "train": [str(f.fold.train[0]), str(f.fold.train[-1])],
                    "test": [str(f.fold.test[0]), str(f.fold.test[-1])],
                    "chosen": str(f.chosen),
                    "train_metrics": asdict(f.train_metrics),
                    "test_metrics": asdict(f.test.metrics()),
                    "test_benchmark": asdict(f.test.benchmark_metrics()),
                    "test_basket": asdict(f.test.basket_metrics()),
                }
                for f in report.walk_forward.folds
            ]
        }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"

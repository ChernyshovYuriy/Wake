"""Walk-forward evaluation: tune parameters on a train window, apply them unchanged to
the following test window, then slide forward. Test windows never overlap, and each train
window ends ``embargo`` sessions early, so no training trade's outcome window (entry +
horizon) reaches into its test window.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from hlsignals.backtest.metrics import Metrics, compute_metrics
from hlsignals.backtest.replay import ReplayResult


@dataclass(frozen=True, slots=True)
class Fold:
    train: tuple[date, ...]
    test: tuple[date, ...]


def make_folds(days: Sequence[date], *, train: int, test: int, embargo: int) -> list[Fold]:
    if train < 1:
        raise ValueError(f"train sessions must be >= 1: {train}")
    if test < 1:
        raise ValueError(f"test sessions must be >= 1: {test}")
    if not 0 <= embargo < train:
        raise ValueError(f"embargo must be in [0, train): {embargo}")
    folds = []
    start = 0
    while start + train < len(days):
        folds.append(
            Fold(
                train=tuple(days[start : start + train - embargo]),
                test=tuple(days[start + train : start + train + test]),
            )
        )
        start += test
    return folds


@dataclass(frozen=True, slots=True)
class FoldResult[P]:
    fold: Fold
    chosen: P
    train_metrics: Metrics
    test: ReplayResult


@dataclass(frozen=True, slots=True)
class WalkForwardResult[P]:
    folds: tuple[FoldResult[P], ...]
    out_of_sample: Metrics
    benchmark: Metrics


def _objective(metrics: Metrics) -> float:
    """Mean net return per trade; a parameter set with no trades ranks last."""
    return metrics.mean if metrics.mean is not None else float("-inf")


class WalkForward[P]:
    def __init__(
        self,
        folds: Sequence[Fold],
        grid: Sequence[P],
        evaluate: Callable[[P, Sequence[date]], ReplayResult],
    ) -> None:
        if not grid:
            raise ValueError("walk-forward needs a non-empty parameter grid")
        self._folds = tuple(folds)
        self._grid = tuple(grid)
        self._evaluate = evaluate

    def run(self) -> WalkForwardResult[P]:
        results = []
        for fold in self._folds:
            scored = [(p, self._evaluate(p, fold.train).metrics()) for p in self._grid]
            chosen, train_metrics = max(scored, key=lambda pm: _objective(pm[1]))
            results.append(
                FoldResult(fold, chosen, train_metrics, self._evaluate(chosen, fold.test))
            )
        trades = [t for r in results for t in r.test.trades]
        sessions = sum(len(r.test.sessions) for r in results)
        exposed = sum(r.test.exposed_sessions for r in results)
        horizon = results[0].test.horizon_sessions if results else 1

        def metrics(returns: list[float]) -> Metrics:
            return compute_metrics(
                returns, horizon_days=horizon, sessions=max(sessions, 1), exposed_sessions=exposed
            )

        return WalkForwardResult(
            folds=tuple(results),
            out_of_sample=metrics([t.net for t in trades]),
            benchmark=metrics([t.benchmark_net for t in trades]),
        )

"""Performance metrics over a list of per-trade net returns (in entry order)."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True, slots=True)
class Metrics:
    n_trades: int
    hit_rate: float | None
    mean: float | None
    median: float | None
    total: float  # sum of trade returns (equal weight per trade)
    sharpe: float | None  # per-trade mean / stdev, annualized by sqrt(252 / horizon)
    max_drawdown: float  # of the cumulative sum of trade returns
    exposure: float  # share of sessions with at least one open trade


def compute_metrics(
    returns: Sequence[float], *, horizon_days: float, sessions: int, exposed_sessions: int
) -> Metrics:
    if sessions < 1:
        raise ValueError(f"sessions must be >= 1: {sessions}")
    exposure = exposed_sessions / sessions
    if not returns:
        return Metrics(0, None, None, None, 0.0, None, 0.0, exposure)
    stdev = statistics.stdev(returns) if len(returns) > 1 else 0.0
    mean = statistics.fmean(returns)
    sharpe = mean / stdev * math.sqrt(TRADING_DAYS_PER_YEAR / horizon_days) if stdev > 0 else None
    cumulative = peak = max_dd = 0.0
    for r in returns:
        cumulative += r
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return Metrics(
        n_trades=len(returns),
        hit_rate=sum(1 for r in returns if r > 0) / len(returns),
        mean=mean,
        median=statistics.median(returns),
        total=sum(returns),
        sharpe=sharpe,
        max_drawdown=max_dd,
        exposure=exposure,
    )

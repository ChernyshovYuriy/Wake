from __future__ import annotations

import math
from datetime import date

import pytest

from hlsignals.backtest.costs import BpsCostModel
from hlsignals.backtest.metrics import compute_metrics
from hlsignals.backtest.prices import PriceBook, ticker_for
from hlsignals.domain.models import DailyBar
from hlsignals.domain.symbols import Symbol

D1, D2, D3 = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)


def bars(ticker: str, *rows: tuple[date, float, float]) -> list[DailyBar]:
    return [DailyBar(ticker, d, o, c) for d, o, c in rows]


def test_price_book_lookups_and_missing_bars() -> None:
    book = PriceBook({"NVDA": bars("NVDA", (D1, 100.0, 101.0), (D3, 103.0, 104.0))})
    assert book.open_on("NVDA", D1) == 100.0
    assert book.close_on("NVDA", D3) == 104.0
    assert book.open_on("NVDA", D2) is None  # missing bar (halt / holiday / vendor gap)
    assert book.close_on("AAPL", D1) is None  # unknown ticker
    assert book.last_day("NVDA") == D3
    assert book.last_day("AAPL") is None


def test_ticker_mapping() -> None:
    overrides = {"PURRDAT": "PURR"}
    assert ticker_for(Symbol("xyz", "NVDA"), overrides) == "NVDA"
    assert ticker_for(Symbol("xyz", "PURRDAT"), overrides) == "PURR"


def test_costs_reduce_returns_exactly_by_modelled_bps() -> None:
    model = BpsCostModel(bps_per_side=5.0)
    assert model.round_trip("NVDA") == pytest.approx(0.0010)
    assert BpsCostModel(0.0).round_trip("NVDA") == 0.0
    with pytest.raises(ValueError, match="bps"):
        BpsCostModel(-1.0)


def test_metrics_zero_trades() -> None:
    m = compute_metrics([], horizon_days=5, sessions=20, exposed_sessions=0)
    assert m.n_trades == 0
    assert m.hit_rate is None
    assert m.mean is None
    assert m.sharpe is None
    assert m.max_drawdown == 0.0
    assert m.exposure == 0.0


def test_metrics_one_trade() -> None:
    m = compute_metrics([0.02], horizon_days=5, sessions=20, exposed_sessions=5)
    assert (m.n_trades, m.hit_rate, m.mean, m.median) == (1, 1.0, 0.02, 0.02)
    assert m.sharpe is None  # no dispersion from one trade
    assert m.exposure == 0.25


def test_metrics_values() -> None:
    returns = [0.02, -0.01, 0.03, -0.04]
    m = compute_metrics(returns, horizon_days=5, sessions=10, exposed_sessions=10)
    assert m.hit_rate == 0.5
    assert m.mean == pytest.approx(0.0)
    assert m.total == pytest.approx(0.0)
    # cumulative 0.02, 0.01, 0.04, 0.00 -> peak 0.04, trough 0.00
    assert m.max_drawdown == pytest.approx(0.04)
    assert m.sharpe == pytest.approx(0.0)


def test_metrics_sharpe_annualized_per_horizon() -> None:
    returns = [0.01, 0.03]
    m = compute_metrics(returns, horizon_days=5, sessions=10, exposed_sessions=2)
    stdev = math.sqrt(((0.01 - 0.02) ** 2 + (0.03 - 0.02) ** 2) / 1)
    assert m.sharpe == pytest.approx(0.02 / stdev * math.sqrt(252 / 5))


def test_metrics_all_flat_returns() -> None:
    m = compute_metrics([0.0, 0.0], horizon_days=5, sessions=10, exposed_sessions=2)
    assert m.hit_rate == 0.0
    assert m.sharpe is None  # zero dispersion


def test_metrics_validation() -> None:
    with pytest.raises(ValueError, match="sessions"):
        compute_metrics([0.1], horizon_days=5, sessions=0, exposed_sessions=0)

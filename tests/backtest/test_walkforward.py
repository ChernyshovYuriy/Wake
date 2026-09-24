from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pytest

from hlsignals.backtest.replay import ReplayResult, Trade
from hlsignals.backtest.walkforward import WalkForward, make_folds
from hlsignals.domain.models import SignalDirection
from tests.backtest.synthetic import SESSIONS, SYMBOLS

DAYS = list(SESSIONS[:50])


def test_folds_are_non_overlapping_cover_the_range_and_embargo_train() -> None:
    folds = make_folds(DAYS, train=20, test=10, embargo=4)
    tests = [d for f in folds for d in f.test]
    assert len(tests) == len(set(tests))  # non-overlapping
    assert tests == DAYS[20:]  # cover everything after the first train window
    for fold in folds:
        assert not set(fold.train) & set(fold.test)
        assert max(fold.train) < min(fold.test)
        assert len(fold.train) == 16  # 20 minus the 4-session embargo
        assert DAYS.index(min(fold.test)) - DAYS.index(max(fold.train)) == 5


def test_last_partial_test_fold_is_kept() -> None:
    folds = make_folds(DAYS[:45], train=20, test=10, embargo=0)
    assert [len(f.test) for f in folds] == [10, 10, 5]


@pytest.mark.parametrize(
    ("train", "test", "embargo", "match"),
    [(0, 10, 0, "train"), (20, 0, 0, "test"), (20, 10, -1, "embargo"), (5, 10, 5, "embargo")],
)
def test_fold_validation(train: int, test: int, embargo: int, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        make_folds(DAYS, train=train, test=test, embargo=embargo)


def test_too_little_data_gives_no_folds() -> None:
    assert make_folds(DAYS[:20], train=20, test=10, embargo=0) == []


def trade(day: date, net: float) -> Trade:
    return Trade(
        day, day, SYMBOLS[0], "AAA", SignalDirection.LONG, 0.5, 100.0, 100.0, net, net, 0.0
    )


def test_parameters_chosen_on_train_only_and_evaluated_out_of_sample() -> None:
    calls: list[tuple[str, date, date]] = []

    def evaluate(param: str, days: Sequence[date]) -> ReplayResult:
        calls.append((param, days[0], days[-1]))
        in_first_half = days[-1] < DAYS[35]
        # "good" wins in training windows before day 35 and loses afterwards; "bad" is the reverse.
        net = 0.01 if (param == "good") == in_first_half else -0.01
        return ReplayResult(tuple(days), tuple(trade(d, net) for d in days), {}, 5)

    result = WalkForward(
        make_folds(DAYS, train=20, test=10, embargo=0), ["bad", "good"], evaluate
    ).run()
    first, second, third = result.folds
    assert first.chosen == "good"  # train 0..19 favours good
    assert first.train_metrics.mean == pytest.approx(0.01)
    assert first.test.metrics().mean == pytest.approx(0.01)  # test 20..29 still good
    assert third.chosen == "bad"  # train 20..39 ends after day 35: favours bad
    for fold in result.folds:  # every test window was evaluated only with the chosen param
        assert (fold.chosen, fold.test.sessions[0], fold.test.sessions[-1]) in calls
    assert result.out_of_sample.n_trades == 30
    assert second.chosen in {"bad", "good"}


def test_empty_grid_rejected() -> None:
    with pytest.raises(ValueError, match="grid"):
        WalkForward([], [], lambda p, d: ReplayResult((), (), {}, 5))


def test_ties_keep_the_first_grid_entry_and_no_trades_rank_last() -> None:
    def evaluate(param: str, days: Sequence[date]) -> ReplayResult:
        trades = () if param == "none" else tuple(trade(d, 0.0) for d in days)
        return ReplayResult(tuple(days), trades, {}, 5)

    folds = make_folds(DAYS[:30], train=20, test=10, embargo=0)
    assert WalkForward(folds, ["none", "a", "b"], evaluate).run().folds[0].chosen == "a"

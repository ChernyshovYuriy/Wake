from __future__ import annotations

from dataclasses import dataclass

import pytest

from hlsignals.core.chain import FilterChain, Verdict


@dataclass
class AtLeast:
    threshold: int
    name: str = "at_least"
    calls: int = 0

    def apply(self, item: int) -> Verdict:
        self.calls += 1
        if item >= self.threshold:
            return Verdict.accept()
        return Verdict.reject(f"{item} < {self.threshold}")


@dataclass
class Even:
    name: str = "even"

    def apply(self, item: int) -> Verdict:
        return Verdict.accept() if item % 2 == 0 else Verdict.reject(f"{item} is odd")


def test_empty_chain_accepts_everything() -> None:
    result = FilterChain[int]([]).run([1, 2])
    assert result.accepted == (1, 2)
    assert result.rejected == ()


def test_short_circuits_at_first_reject_and_records_reason() -> None:
    second = AtLeast(0)
    chain = FilterChain[int]([Even(), second])
    result = chain.run([1, 2])
    assert result.accepted == (2,)
    (rejection,) = result.rejected
    assert (rejection.item, rejection.filter_name, rejection.reason) == (1, "even", "1 is odd")
    assert second.calls == 1  # never saw the rejected item


def test_exact_threshold_accepts() -> None:
    assert FilterChain[int]([AtLeast(5)]).run([5]).accepted == (5,)


def test_counts_by_filter_and_empty_flag() -> None:
    result = FilterChain[int]([AtLeast(10), Even()]).run([1, 2, 11, 12])
    assert result.counts_by_filter() == {"at_least": 2, "even": 1}
    assert not result.is_empty
    assert FilterChain[int]([AtLeast(99)]).run([1]).is_empty


def test_evaluate_single_item() -> None:
    chain = FilterChain[int]([Even()])
    assert chain.evaluate(2) is None
    rejection = chain.evaluate(3)
    assert rejection is not None
    assert rejection.filter_name == "even"


def test_reject_requires_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        Verdict.reject("")


def test_duplicate_filter_names_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        FilterChain[int]([Even(), Even()])

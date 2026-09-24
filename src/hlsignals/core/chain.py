"""Chain of Responsibility for accept/reject filters (markets, wallets).

Filters run in order; the first rejection stops the chain and is recorded with the
filter's name and reason, so every exclusion in a report is explained.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Verdict:
    accepted: bool
    reason: str

    @classmethod
    def accept(cls) -> Verdict:
        return cls(accepted=True, reason="")

    @classmethod
    def reject(cls, reason: str) -> Verdict:
        if not reason:
            raise ValueError("a rejection needs a reason")
        return cls(accepted=False, reason=reason)


class Filter[T](Protocol):
    @property
    def name(self) -> str: ...

    def apply(self, item: T) -> Verdict: ...


@dataclass(frozen=True, slots=True)
class Rejection[T]:
    item: T
    filter_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class ChainResult[T]:
    accepted: tuple[T, ...]
    rejected: tuple[Rejection[T], ...]

    @property
    def is_empty(self) -> bool:
        return not self.accepted

    def counts_by_filter(self) -> dict[str, int]:
        return dict(Counter(r.filter_name for r in self.rejected))


class FilterChain[T]:
    def __init__(self, filters: Sequence[Filter[T]]) -> None:
        names = [f.name for f in filters]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate filter names in chain: {names}")
        self._filters = tuple(filters)

    def evaluate(self, item: T) -> Rejection[T] | None:
        for f in self._filters:
            verdict = f.apply(item)
            if not verdict.accepted:
                return Rejection(item, f.name, verdict.reason)
        return None

    def run(self, items: Iterable[T]) -> ChainResult[T]:
        accepted: list[T] = []
        rejected: list[Rejection[T]] = []
        for item in items:
            rejection = self.evaluate(item)
            if rejection is None:
                accepted.append(item)
            else:
                rejected.append(rejection)
        return ChainResult(tuple(accepted), tuple(rejected))

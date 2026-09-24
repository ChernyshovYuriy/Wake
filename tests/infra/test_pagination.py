from __future__ import annotations

from dataclasses import replace

import pytest

from hlsignals.domain.models import Fill
from hlsignals.infra.pagination import FillPaging, FillsIterator
from tests.factories import T0_MS, make_fill

PAGE = 3


class Pages:
    """A fake paged endpoint over ``fills``: returns up to PAGE fills with time >= start."""

    def __init__(self, fills: list[Fill]) -> None:
        self.fills = fills
        self.requests: list[tuple[int, int | None]] = []

    def __call__(self, start_ms: int, end_ms: int | None) -> list[Fill]:
        self.requests.append((start_ms, end_ms))
        window = [
            f
            for f in self.fills
            if f.time_ms >= start_ms and (end_ms is None or f.time_ms <= end_ms)
        ]
        return window[:PAGE]


def fills_at(*times: int) -> list[Fill]:
    return [make_fill(time_ms=T0_MS + t) for t in times]


def iterate(
    fills: list[Fill], history_cap: int = 100, end_ms: int | None = None
) -> tuple[list[Fill], FillsIterator, Pages]:
    pages = Pages(fills)
    it = FillsIterator(pages, T0_MS, end_ms, FillPaging(PAGE, history_cap))
    return list(it), it, pages


def test_empty() -> None:
    got, it, pages = iterate([])
    assert got == []
    assert not it.truncated
    assert pages.requests == [(T0_MS, None)]


def test_single_short_page() -> None:
    fills = fills_at(0, 1)
    got, it, pages = iterate(fills)
    assert got == fills
    assert len(pages.requests) == 1
    assert not it.truncated


def test_exact_page_size_boundary_needs_one_more_request() -> None:
    fills = fills_at(0, 1, 2)
    got, _, pages = iterate(fills)
    assert got == fills
    assert pages.requests == [(T0_MS, None), (T0_MS + 2, None)]  # inclusive restart


def test_multi_page_with_boundary_duplicates_removed() -> None:
    fills = fills_at(0, 1, 2, 3, 4, 5, 6)
    got, it, _ = iterate(fills)
    assert got == fills
    assert len({f.dedupe_key for f in got}) == len(got)
    assert it.pages_fetched == 4


def test_same_millisecond_fills_across_boundary_are_not_lost() -> None:
    fills = fills_at(0, 1, 2, 2, 2, 3)
    got, _, _ = iterate(fills)
    assert got == fills


def test_page_entirely_in_one_millisecond_advances_and_flags_truncation() -> None:
    fills = fills_at(5, 5, 5, 5, 6)
    got, it, _ = iterate(fills)
    assert it.truncated  # the fourth fill at t=5 could not be reached
    assert got[-1].time_ms == T0_MS + 6


def test_end_bound_is_passed_through() -> None:
    got, _, pages = iterate(fills_at(0, 1, 9), end_ms=T0_MS + 1)
    assert [f.time_ms - T0_MS for f in got] == [0, 1]
    assert pages.requests[0] == (T0_MS, T0_MS + 1)


def test_end_before_start_rejected() -> None:
    with pytest.raises(ValueError, match="end"):
        FillsIterator(Pages([]), T0_MS, T0_MS - 1, FillPaging(PAGE, 10))


@pytest.mark.parametrize(("page_cap", "history_cap"), [(0, 10), (5, 4)])
def test_caps_validated(page_cap: int, history_cap: int) -> None:
    with pytest.raises(ValueError, match="cap"):
        FillPaging(page_cap=page_cap, history_cap=history_cap)


def test_history_cap_sets_truncated() -> None:
    _, it, _ = iterate(fills_at(0, 1, 2, 3), history_cap=4)
    assert it.truncated


def test_is_lazy_and_its_own_iterator() -> None:
    pages = Pages(fills_at(0, 1, 2, 3, 4))
    it = FillsIterator(pages, T0_MS, None, FillPaging(PAGE, 100))
    assert iter(it) is it
    assert pages.requests == []
    next(it)
    assert len(pages.requests) == 1


def test_duplicate_tids_with_different_hash_are_distinct() -> None:
    a = make_fill(time_ms=T0_MS)
    b = replace(a, hash="0x" + "f" * 64)
    got, _, _ = iterate([a, b])
    assert got == [a, b]

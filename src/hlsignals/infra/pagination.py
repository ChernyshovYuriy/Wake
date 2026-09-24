"""Iterator over ``userFillsByTime`` pages (docs/api-notes.md §5).

Pages hold at most ``page_cap`` fills in ascending time. ``startTime`` is inclusive, so
the next page restarts at the last fill's time and duplicates at the boundary are dropped
by ``(tid, hash)``; restarting one millisecond later would lose other fills sharing that
millisecond. The API only retains the most recent ``history_cap`` fills, so reaching that
many means older history may be missing: ``truncated`` becomes True.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from hlsignals.domain.models import Fill

PageFetcher = Callable[[int, int | None], list[Fill]]  # (start_ms, end_ms) -> one page


@dataclass(frozen=True, slots=True)
class FillPaging:
    page_cap: int
    history_cap: int

    def __post_init__(self) -> None:
        if self.page_cap < 1 or self.history_cap < self.page_cap:
            raise ValueError(
                f"need 1 <= page_cap <= history_cap, got {self.page_cap}, {self.history_cap}"
            )


class FillsIterator(Iterator[Fill]):
    def __init__(
        self, fetch_page: PageFetcher, start_ms: int, end_ms: int | None, paging: FillPaging
    ) -> None:
        if end_ms is not None and end_ms < start_ms:
            raise ValueError(f"end ({end_ms}) precedes start ({start_ms})")
        self._fetch_page = fetch_page
        self._start_ms = start_ms
        self._end_ms = end_ms
        self._paging = paging
        self._fills = self._generate()
        self.truncated = False
        self.pages_fetched = 0

    def __iter__(self) -> FillsIterator:
        return self

    def __next__(self) -> Fill:
        return next(self._fills)

    def _generate(self) -> Iterator[Fill]:
        seen: set[tuple[int, str]] = set()
        start = self._start_ms
        while True:
            page = self._fetch_page(start, self._end_ms)
            self.pages_fetched += 1
            for fill in page:
                if fill.dedupe_key not in seen:
                    seen.add(fill.dedupe_key)
                    self.truncated |= len(seen) >= self._paging.history_cap
                    yield fill
            if len(page) < self._paging.page_cap:
                return
            last = page[-1].time_ms
            if last == start:
                # A full page inside one millisecond: the rest of that ms is unreachable.
                self.truncated = True
                last += 1
            start = last

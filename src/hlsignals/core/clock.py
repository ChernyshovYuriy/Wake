"""The only module that knows how to read the wall clock or sleep (CLAUDE.md rule 5)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

MS_PER_SECOND = 1000
MS_PER_DAY = 86_400 * MS_PER_SECOND


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, timezone-aware UTC."""
        ...


class Sleeper(Protocol):
    def sleep(self, seconds: float) -> None: ...


def _require_aware(dt: datetime) -> None:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")


def _require_non_negative(seconds: float) -> None:
    if seconds < 0:
        raise ValueError(f"cannot sleep a negative duration: {seconds}")


def to_ms(dt: datetime) -> int:
    """Epoch milliseconds of an aware datetime."""
    _require_aware(dt)
    delta = dt - datetime(1970, 1, 1, tzinfo=UTC)
    return delta // timedelta(milliseconds=1)


def from_ms(ms: int) -> datetime:
    """Aware UTC datetime from epoch milliseconds."""
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)


def ms_to_days(ms: int) -> float:
    return ms / MS_PER_DAY


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SystemSleeper:
    def sleep(self, seconds: float) -> None:
        _require_non_negative(seconds)
        time.sleep(seconds)


class FakeClock:
    """Deterministic clock for tests and backtests."""

    def __init__(self, start: datetime) -> None:
        _require_aware(start)
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, t: datetime) -> None:
        _require_aware(t)
        self._now = t

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError(f"clock cannot move backwards: {delta}")
        self._now += delta


class FakeSleeper:
    """Records sleep durations; advances ``clock`` by each one when given."""

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.calls: list[float] = []
        self._clock = clock

    def sleep(self, seconds: float) -> None:
        _require_non_negative(seconds)
        self.calls.append(seconds)
        if self._clock is not None:
            self._clock.advance(timedelta(seconds=seconds))

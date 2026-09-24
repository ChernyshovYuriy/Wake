"""US equity session calendar: last close, next open, is-open, sessions.

Holidays and early closes come from a HolidayProvider (default: a static table built from
config/us_market_calendar.toml). Days outside the provider's coverage raise ConfigError,
so a stale table fails loudly instead of silently treating a holiday as a session.
Local session times are converted with zoneinfo, so DST transitions are exact.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hlsignals.core.clock import require_aware
from hlsignals.core.errors import ConfigError

_ONE_DAY = timedelta(days=1)
_SATURDAY = 5


def _is_weekend(day: date) -> bool:
    return day.weekday() >= _SATURDAY


@dataclass(frozen=True, slots=True)
class Session:
    day: date  # local (exchange) date
    open: datetime  # UTC
    close: datetime  # UTC


class HolidayProvider(Protocol):
    def check_covered(self, day: date) -> None:
        """Raise ConfigError if the provider has no data for ``day``."""
        ...

    def is_holiday(self, day: date) -> bool: ...

    def early_close(self, day: date) -> time | None: ...


class SessionCalendar(Protocol):
    def is_open(self, t: datetime) -> bool: ...

    def last_close(self, t: datetime) -> datetime: ...

    def next_open(self, t: datetime) -> datetime: ...

    def sessions(self, start: date, end: date) -> list[Session]: ...


@dataclass(frozen=True, slots=True)
class StaticHolidayTable:
    first_day: date
    last_day: date
    holidays: frozenset[date]
    early_closes: Mapping[date, time] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.first_day > self.last_day:
            raise ConfigError(f"first_day {self.first_day} is after last_day {self.last_day}")
        for day in (*self.holidays, *self.early_closes):
            if not self.first_day <= day <= self.last_day:
                raise ConfigError(f"calendar date {day} is outside the covered range")
            if _is_weekend(day):
                raise ConfigError(f"calendar date {day} falls on a weekend")
        both = self.holidays & set(self.early_closes)
        if both:
            raise ConfigError(f"dates are both holiday and early close: {sorted(both)}")

    def check_covered(self, day: date) -> None:
        if not self.first_day <= day <= self.last_day:
            raise ConfigError(
                f"market calendar covers {self.first_day}..{self.last_day}; {day} is outside "
                "(extend config/us_market_calendar.toml)"
            )

    def is_holiday(self, day: date) -> bool:
        return day in self.holidays

    def early_close(self, day: date) -> time | None:
        return self.early_closes.get(day)


class UsEquityCalendar:
    def __init__(
        self,
        holidays: StaticHolidayTable,
        session_open: time,
        session_close: time,
        timezone: str,
    ) -> None:
        if session_open >= session_close:
            raise ConfigError(f"session_open {session_open} must precede session_close")
        try:
            self._tz = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(f"unknown timezone {timezone!r}") from exc
        for day, close in holidays.early_closes.items():
            if not session_open < close < session_close:
                raise ConfigError(f"early close {close} on {day} is outside the session")
        self.holidays = holidays
        self._open = session_open
        self._close = session_close

    def session(self, day: date) -> Session | None:
        """The session on local ``day``, or None if the market is closed all day."""
        self.holidays.check_covered(day)
        if _is_weekend(day) or self.holidays.is_holiday(day):
            return None
        close = self.holidays.early_close(day) or self._close
        return Session(day, self._utc(day, self._open), self._utc(day, close))

    def is_open(self, t: datetime) -> bool:
        session = self.session(self._local_day(t))
        return session is not None and session.open <= t < session.close

    def last_close(self, t: datetime) -> datetime:
        """The most recent session close at or before ``t``."""
        day = self._local_day(t)
        while True:
            session = self.session(day)
            if session is not None and session.close <= t:
                return session.close
            day -= _ONE_DAY

    def next_open(self, t: datetime) -> datetime:
        """The earliest session open at or after ``t``."""
        day = self._local_day(t)
        while True:
            session = self.session(day)
            if session is not None and session.open >= t:
                return session.open
            day += _ONE_DAY

    def sessions(self, start: date, end: date) -> list[Session]:
        """Sessions on local days ``start``..``end`` inclusive."""
        if end < start:
            raise ValueError(f"end {end} precedes start {start}")
        days = (start + i * _ONE_DAY for i in range((end - start).days + 1))
        return [s for s in (self.session(d) for d in days) if s is not None]

    def _local_day(self, t: datetime) -> date:
        require_aware(t)
        return t.astimezone(self._tz).date()

    def _utc(self, day: date, at: time) -> datetime:
        return datetime.combine(day, at, tzinfo=self._tz).astimezone(UTC)


def calendar_from_mapping(raw: Mapping[str, Any]) -> UsEquityCalendar:
    """Build the calendar from parsed config (see config/us_market_calendar.toml)."""
    holidays = _typed_list(raw, "holidays", date)
    early = _typed_list(raw, "early_closes", dict)
    table = StaticHolidayTable(
        first_day=_typed(raw, "first_day", date),
        last_day=_typed(raw, "last_day", date),
        holidays=frozenset(holidays),
        early_closes={_typed(e, "day", date): _typed(e, "close", time) for e in early},
    )
    return UsEquityCalendar(
        table,
        _typed(raw, "session_open", time),
        _typed(raw, "session_close", time),
        _typed(raw, "timezone", str),
    )


def _typed[T](raw: Mapping[str, Any], key: str, kind: type[T]) -> T:
    if key not in raw:
        raise ConfigError(f"market calendar config is missing {key!r}")
    value = raw[key]
    if not isinstance(value, kind):
        raise ConfigError(f"market calendar {key!r} must be {kind.__name__}, got {value!r}")
    return value


def _typed_list[T](raw: Mapping[str, Any], key: str, kind: type[T]) -> list[T]:
    values = _typed(raw, key, list)
    for value in values:
        if not isinstance(value, kind):
            raise ConfigError(f"market calendar {key!r} entries must be {kind.__name__}")
    return values

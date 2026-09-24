"""DST / holiday / early-close matrix against the real config/us_market_calendar.toml."""

from __future__ import annotations

import tomllib
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hlsignals.core.clock import from_ms, to_ms
from hlsignals.core.errors import ConfigError
from hlsignals.session.calendar import (
    Session,
    StaticHolidayTable,
    UsEquityCalendar,
    calendar_from_mapping,
)

CONFIG = Path(__file__).parents[2] / "config" / "us_market_calendar.toml"
RAW = tomllib.loads(CONFIG.read_text())
CAL = calendar_from_mapping(RAW)


def utc(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


# --- is_open --------------------------------------------------------------------------
# 2026-09-24 is a Thursday in EDT (UTC-4): session 13:30-20:00 UTC.


@pytest.mark.parametrize(
    ("t", "expected"),
    [
        (utc(2026, 9, 24, 13, 29), False),  # 09:29 ET
        (utc(2026, 9, 24, 13, 30), True),  # exactly 09:30: open
        (utc(2026, 9, 24, 17, 0), True),  # mid-session
        (utc(2026, 9, 24, 19, 59), True),
        (utc(2026, 9, 24, 20, 0), False),  # exactly 16:00: closed
        (utc(2026, 9, 26, 15, 0), False),  # Saturday
        (utc(2026, 9, 27, 15, 0), False),  # Sunday
        (utc(2026, 4, 3, 15, 0), False),  # Good Friday
        (utc(2026, 11, 26, 15, 0), False),  # Thanksgiving
        (utc(2026, 11, 27, 17, 59), True),  # day after Thanksgiving, 12:59 EST
        (utc(2026, 11, 27, 18, 0), False),  # early close 13:00 EST
        (utc(2025, 1, 9, 16, 0), False),  # special closure
    ],
)
def test_is_open(t: datetime, expected: bool) -> None:
    assert CAL.is_open(t) is expected


# --- last_close / next_open -------------------------------------------------------------


def test_after_close_same_day() -> None:
    assert CAL.last_close(utc(2026, 9, 24, 22)) == utc(2026, 9, 24, 20)


def test_exactly_at_close_is_that_close() -> None:
    assert CAL.last_close(utc(2026, 9, 24, 20)) == utc(2026, 9, 24, 20)


def test_before_open_uses_previous_session() -> None:
    assert CAL.last_close(utc(2026, 9, 24, 12)) == utc(2026, 9, 23, 20)


def test_mid_session_last_close_is_yesterday() -> None:
    assert CAL.last_close(utc(2026, 9, 24, 17)) == utc(2026, 9, 23, 20)


def test_monday_pre_open_goes_back_to_friday() -> None:
    assert CAL.last_close(utc(2026, 9, 28, 12)) == utc(2026, 9, 25, 20)
    assert CAL.next_open(utc(2026, 9, 26, 12)) == utc(2026, 9, 28, 13, 30)


def test_holiday_is_skipped() -> None:
    # Tue after Labor Day (Mon 2026-09-07) pre-open -> Fri Sep 4 close; next open Tue.
    assert CAL.last_close(utc(2026, 9, 8, 12)) == utc(2026, 9, 4, 20)
    assert CAL.next_open(utc(2026, 9, 7, 12)) == utc(2026, 9, 8, 13, 30)


def test_thanksgiving_then_early_close() -> None:
    # Friday after Thanksgiving closes 13:00 EST = 18:00 UTC.
    assert CAL.last_close(utc(2026, 11, 28, 12)) == utc(2026, 11, 27, 18)
    assert CAL.last_close(utc(2026, 11, 27, 12)) == utc(2026, 11, 25, 21)


def test_next_open_at_exact_open_is_now_and_after_open_is_next_day() -> None:
    assert CAL.next_open(utc(2026, 9, 24, 13, 30)) == utc(2026, 9, 24, 13, 30)
    assert CAL.next_open(utc(2026, 9, 24, 13, 31)) == utc(2026, 9, 25, 13, 30)


# --- DST ---------------------------------------------------------------------------------
# 2026: spring forward Sun Mar 8, fall back Sun Nov 1.


def test_spring_forward_week() -> None:
    assert CAL.last_close(utc(2026, 3, 9, 12)) == utc(2026, 3, 6, 21)  # Fri close in EST
    assert CAL.next_open(utc(2026, 3, 8, 12)) == utc(2026, 3, 9, 13, 30)  # Mon open in EDT


def test_fall_back_week() -> None:
    assert CAL.last_close(utc(2026, 11, 2, 12)) == utc(2026, 10, 30, 20)  # Fri close in EDT
    assert CAL.next_open(utc(2026, 11, 1, 12)) == utc(2026, 11, 2, 14, 30)  # Mon open in EST


def test_is_open_on_dst_boundary_mondays() -> None:
    assert CAL.is_open(utc(2026, 3, 9, 13, 30))  # 09:30 EDT
    assert not CAL.is_open(utc(2026, 11, 2, 13, 30))  # 08:30 EST


# --- sessions ----------------------------------------------------------------------------


def test_sessions_over_thanksgiving_week() -> None:
    sessions = CAL.sessions(date(2026, 11, 23), date(2026, 11, 29))
    assert [s.day for s in sessions] == [
        date(2026, 11, 23),
        date(2026, 11, 24),
        date(2026, 11, 25),
        date(2026, 11, 27),
    ]
    assert sessions[-1] == Session(
        date(2026, 11, 27), utc(2026, 11, 27, 14, 30), utc(2026, 11, 27, 18)
    )


def test_sessions_empty_and_inverted_range() -> None:
    assert CAL.sessions(date(2026, 9, 26), date(2026, 9, 27)) == []
    with pytest.raises(ValueError, match="end"):
        CAL.sessions(date(2026, 9, 2), date(2026, 9, 1))


# --- validation and coverage ---------------------------------------------------------------


def test_naive_input_rejected() -> None:
    naive = datetime(2026, 9, 24, 12)  # noqa: DTZ001 - deliberately naive
    for method in (CAL.is_open, CAL.last_close, CAL.next_open):
        with pytest.raises(ValueError, match="naive"):
            method(naive)


def test_outside_coverage_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="covers"):
        CAL.last_close(utc(2025, 1, 2, 12))  # would need 2024-12-31
    with pytest.raises(ConfigError, match="covers"):
        CAL.next_open(utc(2028, 12, 29, 22))  # would need 2029
    with pytest.raises(ConfigError, match="covers"):
        CAL.is_open(utc(2030, 1, 2, 15))


def test_table_facts() -> None:
    table = CAL.holidays
    assert all(d.weekday() < 5 for d in table.holidays)
    assert not set(table.early_closes) & table.holidays
    assert table.early_close(date(2026, 12, 24)) == time(13)
    assert table.early_close(date(2026, 12, 23)) is None


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"last_day": date(2024, 1, 1)}, "first_day"),
        ({"holidays": [date(2030, 1, 2)]}, "outside"),
        ({"holidays": [date(2026, 9, 26)]}, "weekend"),
        ({"early_closes": [{"day": date(2026, 9, 24), "close": time(17)}]}, "early close"),
        ({"session_open": time(16), "session_close": time(9, 30)}, "session_open"),
        ({"timezone": "Mars/Base"}, "timezone"),
        ({"holidays": "2026-01-01"}, "holidays"),
        ({"holidays": ["2026-01-02"]}, "entries must be date"),
        ({"early_closes": [{"day": date(2026, 1, 1), "close": time(13)}]}, "both holiday"),
    ],
)
def test_invalid_config(patch: dict[str, object], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        calendar_from_mapping({**RAW, **patch})


def test_missing_key() -> None:
    raw = dict(RAW)
    del raw["timezone"]
    with pytest.raises(ConfigError, match="timezone"):
        calendar_from_mapping(raw)


def test_calendar_parts_constructible_directly() -> None:
    table = StaticHolidayTable(date(2026, 1, 1), date(2026, 12, 31), frozenset(), {})
    cal = UsEquityCalendar(table, time(9, 30), time(16), "America/New_York")
    assert cal.is_open(utc(2026, 9, 24, 15))


# --- properties ---------------------------------------------------------------------------

moments = st.integers(to_ms(utc(2025, 1, 10, 0)), to_ms(utc(2028, 12, 20, 0))).map(from_ms)


@given(moments)
def test_last_close_is_not_after_t_and_market_closed_at_it(t: datetime) -> None:
    close = CAL.last_close(t)
    assert close <= t
    assert not CAL.is_open(close)
    assert CAL.is_open(close - timedelta(minutes=1))


@given(moments)
def test_next_open_is_not_before_t_and_market_open_at_it(t: datetime) -> None:
    opens = CAL.next_open(t)
    assert opens >= t
    assert CAL.is_open(opens)
    assert not CAL.is_open(opens - timedelta(minutes=1))

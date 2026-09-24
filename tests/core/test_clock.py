from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hlsignals.core.clock import (
    MS_PER_DAY,
    FakeClock,
    FakeSleeper,
    SystemClock,
    SystemSleeper,
    from_ms,
    ms_to_days,
    to_ms,
)

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
NAIVE = datetime(2026, 9, 24, 12, 0)  # noqa: DTZ001 - deliberately naive


def test_round_trip() -> None:
    assert from_ms(to_ms(T0)) == T0


@given(st.integers(min_value=0, max_value=253_402_300_799_000))
def test_round_trip_property(ms: int) -> None:
    assert to_ms(from_ms(ms)) == ms


def test_epoch_zero() -> None:
    assert to_ms(datetime(1970, 1, 1, tzinfo=UTC)) == 0
    assert from_ms(0) == datetime(1970, 1, 1, tzinfo=UTC)


def test_far_future() -> None:
    far = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert from_ms(to_ms(far)) == far


def test_non_utc_aware_is_converted() -> None:
    plus2 = T0.astimezone(timezone(timedelta(hours=2)))
    assert to_ms(plus2) == to_ms(T0)


def test_from_ms_returns_utc() -> None:
    assert from_ms(0).tzinfo is UTC


def test_to_ms_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_ms(NAIVE)


def test_ms_to_days() -> None:
    assert ms_to_days(MS_PER_DAY) == 1.0
    assert ms_to_days(MS_PER_DAY // 2) == 0.5


def test_system_clock_is_aware_utc() -> None:
    assert SystemClock().now().tzinfo is UTC


def test_fake_clock_advance() -> None:
    clock = FakeClock(T0)
    clock.advance(timedelta(minutes=5))
    assert clock.now() == T0 + timedelta(minutes=5)


def test_fake_clock_rejects_naive_and_backwards() -> None:
    with pytest.raises(ValueError, match="naive"):
        FakeClock(NAIVE)
    with pytest.raises(ValueError, match="backwards"):
        FakeClock(T0).advance(timedelta(seconds=-1))


def test_fake_clock_set() -> None:
    clock = FakeClock(T0)
    clock.set(T0 + timedelta(days=1))
    assert clock.now() == T0 + timedelta(days=1)
    with pytest.raises(ValueError, match="naive"):
        clock.set(NAIVE)


def test_fake_sleeper_records_and_advances_clock() -> None:
    clock = FakeClock(T0)
    sleeper = FakeSleeper(clock)
    sleeper.sleep(1.5)
    sleeper.sleep(0)
    assert sleeper.calls == [1.5, 0]
    assert clock.now() == T0 + timedelta(seconds=1.5)


def test_fake_sleeper_without_clock() -> None:
    sleeper = FakeSleeper()
    sleeper.sleep(2)
    assert sleeper.calls == [2]


@pytest.mark.parametrize("sleeper", [FakeSleeper(), SystemSleeper()])
def test_sleepers_reject_negative(sleeper: FakeSleeper | SystemSleeper) -> None:
    with pytest.raises(ValueError, match="negative"):
        sleeper.sleep(-1)


def test_system_sleeper_zero_returns() -> None:
    SystemSleeper().sleep(0)

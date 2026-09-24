from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest

from hlsignals.core.clock import FakeClock, FakeSleeper
from hlsignals.core.errors import AdapterError, NonRetryableError, RetryableError
from hlsignals.infra.transport import (
    CachingTransport,
    FixtureTransport,
    HttpTransport,
    Payload,
    RateLimitedTransport,
    RetryingTransport,
    RetryPolicy,
    Transport,
    WeightTable,
    canonical_key,
)
from tests.factories import AS_OF

URL = "https://api.example/info"
PAYLOAD = {"type": "perpDexs"}


def http_with(handler: Callable[[httpx.Request], httpx.Response]) -> HttpTransport:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpTransport(URL, timeout_s=5.0, client=client)


def raising(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


class Scripted(Transport):
    """Returns/raises a scripted sequence of outcomes and records every payload."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def post(self, payload: Payload) -> Any:
        self.calls.append(dict(payload))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# --- canonical key -----------------------------------------------------------------


def test_canonical_key_is_stable_under_dict_ordering() -> None:
    assert canonical_key({"a": 1, "b": {"x": 1, "y": 2}}) == canonical_key(
        {"b": {"y": 2, "x": 1}, "a": 1}
    )
    assert canonical_key({"a": 1}) != canonical_key({"a": 2})


# --- HttpTransport ------------------------------------------------------------------


def test_http_posts_json_and_returns_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[1, 2])

    assert http_with(handler).post(PAYLOAD) == [1, 2]
    assert json.loads(seen[0].content) == PAYLOAD
    assert str(seen[0].url) == URL


def test_http_invalid_json_is_adapter_error() -> None:
    with pytest.raises(AdapterError, match="JSON"):
        http_with(lambda r: httpx.Response(200, text="not json")).post(PAYLOAD)


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_http_retryable_statuses(status: int) -> None:
    with pytest.raises(RetryableError) as info:
        http_with(lambda r: httpx.Response(status, text="x")).post(PAYLOAD)
    assert info.value.status == status


@pytest.mark.parametrize("status", [400, 404, 422])
def test_http_non_retryable_statuses(status: int) -> None:
    with pytest.raises(NonRetryableError) as info:
        http_with(lambda r: httpx.Response(status, text="bad")).post(PAYLOAD)
    assert info.value.status == status


@pytest.mark.parametrize(
    "exc", [httpx.ReadTimeout("slow"), httpx.ConnectError("down"), httpx.RemoteProtocolError("x")]
)
def test_http_network_failures_are_retryable(exc: Exception) -> None:
    with pytest.raises(RetryableError):
        http_with(raising(exc)).post(PAYLOAD)


def test_http_creates_own_client_when_none_given() -> None:
    transport = HttpTransport(URL, timeout_s=1.0)
    assert transport.url == URL


# --- RetryingTransport --------------------------------------------------------------

POLICY = RetryPolicy(max_attempts=4, base_delay_s=0.5, multiplier=2.0, max_delay_s=1.5)


def test_retry_policy_delays_are_exponential_and_capped() -> None:
    assert POLICY.delays() == [0.5, 1.0, 1.5]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"max_attempts": 0}, "max_attempts"),
        ({"base_delay_s": -1.0}, "delay"),
        ({"max_delay_s": -1.0}, "delay"),
        ({"multiplier": 0.5}, "multiplier"),
    ],
)
def test_retry_policy_validation(kwargs: dict[str, Any], match: str) -> None:
    fields = {"max_attempts": 3, "base_delay_s": 1.0, "multiplier": 2.0, "max_delay_s": 10.0}
    with pytest.raises(ValueError, match=match):
        RetryPolicy(**{**fields, **kwargs})


def test_retry_success_first_try_does_not_sleep() -> None:
    sleeper = FakeSleeper()
    assert RetryingTransport(Scripted("ok"), POLICY, sleeper).post(PAYLOAD) == "ok"
    assert sleeper.calls == []


def test_retry_transient_then_success() -> None:
    inner = Scripted(RetryableError("x"), RetryableError("y"), "ok")
    sleeper = FakeSleeper()
    assert RetryingTransport(inner, POLICY, sleeper).post(PAYLOAD) == "ok"
    assert sleeper.calls == [0.5, 1.0]
    assert len(inner.calls) == 3


def test_retry_exhaustion_raises_last_error() -> None:
    last = RetryableError("last")
    inner = Scripted(RetryableError("a"), RetryableError("b"), RetryableError("c"), last)
    sleeper = FakeSleeper()
    with pytest.raises(RetryableError) as info:
        RetryingTransport(inner, POLICY, sleeper).post(PAYLOAD)
    assert info.value is last
    assert sleeper.calls == [0.5, 1.0, 1.5]


def test_retry_does_not_retry_non_retryable() -> None:
    inner = Scripted(NonRetryableError("no"))
    sleeper = FakeSleeper()
    with pytest.raises(NonRetryableError):
        RetryingTransport(inner, POLICY, sleeper).post(PAYLOAD)
    assert sleeper.calls == []
    assert len(inner.calls) == 1


# --- RateLimitedTransport -----------------------------------------------------------

WEIGHTS = WeightTable(
    default_base=20, base={"l2Book": 2}, items_per_extra_unit={"userFillsByTime": 20}
)


def limited(
    inner: Transport, budget: int = 50
) -> tuple[RateLimitedTransport, FakeClock, FakeSleeper]:
    clock = FakeClock(AS_OF)
    sleeper = FakeSleeper(clock)
    transport = RateLimitedTransport(
        inner, budget=budget, window_s=60.0, weights=WEIGHTS, clock=clock, sleeper=sleeper
    )
    return transport, clock, sleeper


def test_weight_table() -> None:
    assert WEIGHTS.base_weight({"type": "l2Book"}) == 2
    assert WEIGHTS.base_weight({"type": "perpDexs"}) == 20
    assert WEIGHTS.extra_weight({"type": "userFillsByTime"}, list(range(45))) == 2
    assert WEIGHTS.extra_weight({"type": "userFillsByTime"}, {"not": "a list"}) == 0
    assert WEIGHTS.extra_weight({"type": "perpDexs"}, list(range(45))) == 0


def test_rate_limit_under_budget_does_not_wait() -> None:
    transport, _, sleeper = limited(Scripted("a", "b"))
    transport.post(PAYLOAD)
    transport.post(PAYLOAD)  # 40 <= 50
    assert sleeper.calls == []


def test_rate_limit_over_budget_waits_exact_time_then_window_resets() -> None:
    transport, clock, sleeper = limited(Scripted("a", "b", "c"))
    transport.post(PAYLOAD)  # t=0, used 20
    clock.advance(timedelta(seconds=10))
    transport.post(PAYLOAD)  # t=10, used 40
    transport.post(PAYLOAD)  # needs 60 > 50: wait until t=60 when the first charge expires
    assert sleeper.calls == [50.0]


def test_rate_limit_per_type_weights() -> None:
    transport, _, sleeper = limited(Scripted(*"abcdefghijklmnopqrstuvwxy"))
    for _ in range(25):
        transport.post({"type": "l2Book"})  # 25 * 2 = 50 <= 50
    assert sleeper.calls == []


def test_rate_limit_charges_response_size_after_the_call() -> None:
    fills = list(range(40))  # +2 units
    transport, _, sleeper = limited(Scripted(fills, "next"))
    transport.post({"type": "userFillsByTime"})  # 20 base + 2 extra = 22
    transport.post(PAYLOAD)  # 42 <= 50: no wait
    assert sleeper.calls == []


def test_rate_limit_rejects_request_heavier_than_budget() -> None:
    with pytest.raises(ValueError, match="budget"):
        limited(Scripted(), budget=10)


def test_retry_outside_rate_limit_charges_every_attempt() -> None:
    inner = Scripted(RetryableError("429", status=429), "ok")
    rate, clock, rate_sleeper = limited(inner, budget=30)
    retry_sleeper = FakeSleeper(clock)
    stacked = RetryingTransport(rate, POLICY, retry_sleeper)
    assert stacked.post(PAYLOAD) == "ok"
    assert retry_sleeper.calls == [0.5]
    # Both attempts were charged: the second one had to wait for the window.
    assert rate_sleeper.calls == [pytest.approx(60.0 - 0.5)]


# --- CachingTransport ---------------------------------------------------------------


def caching(inner: Transport) -> tuple[CachingTransport, FakeClock]:
    clock = FakeClock(AS_OF)
    return CachingTransport(inner, ttl_s=30.0, clock=clock), clock


def test_cache_miss_then_hit() -> None:
    inner = Scripted({"v": 1})
    transport, _ = caching(inner)
    assert transport.post(PAYLOAD) == {"v": 1}
    assert transport.post(PAYLOAD) == {"v": 1}
    assert len(inner.calls) == 1


def test_cache_hit_is_independent_copy() -> None:
    transport, _ = caching(Scripted({"v": [1]}))
    transport.post(PAYLOAD)["v"].append(2)
    assert transport.post(PAYLOAD) == {"v": [1]}


def test_cache_ttl_expiry() -> None:
    inner = Scripted("old", "new")
    transport, clock = caching(inner)
    transport.post(PAYLOAD)
    clock.advance(timedelta(seconds=30))
    assert transport.post(PAYLOAD) == "new"


def test_cache_keys_differ_by_payload_and_ignore_key_order() -> None:
    inner = Scripted("a", "b")
    transport, _ = caching(inner)
    assert transport.post({"type": "x", "dex": "a"}) == "a"
    assert transport.post({"dex": "a", "type": "x"}) == "a"
    assert transport.post({"type": "x", "dex": "b"}) == "b"


def test_cache_does_not_cache_errors() -> None:
    inner = Scripted(RetryableError("x"), "ok")
    transport, _ = caching(inner)
    with pytest.raises(RetryableError):
        transport.post(PAYLOAD)
    assert transport.post(PAYLOAD) == "ok"


def test_cache_rejects_non_positive_ttl() -> None:
    with pytest.raises(ValueError, match="ttl"):
        CachingTransport(Scripted(), ttl_s=0, clock=FakeClock(AS_OF))


# --- FixtureTransport ---------------------------------------------------------------


def test_fixture_serves_known_payload(fixture_dir: Any) -> None:
    dexes = FixtureTransport(fixture_dir).post({"type": "perpDexs"})
    assert dexes[0] is None
    assert dexes[1]["name"] == "xyz"


def test_fixture_unknown_payload_errors(fixture_dir: Any) -> None:
    with pytest.raises(NonRetryableError, match="no fixture"):
        FixtureTransport(fixture_dir).post({"type": "perpDexs", "extra": 1})


@pytest.mark.parametrize(
    ("name", "error"),
    [("error_candles_bare_coin", RetryableError), ("error_unknown_type", NonRetryableError)],
)
def test_fixture_replays_recorded_http_errors(
    fixture_dir: Any, name: str, error: type[Exception]
) -> None:
    request = json.loads((fixture_dir / f"{name}.json").read_text())["request"]
    with pytest.raises(error):
        FixtureTransport(fixture_dir).post(request)


def test_fixture_rejects_duplicate_requests(tmp_path: Any) -> None:
    doc = json.dumps({"request": PAYLOAD, "response": 1})
    (tmp_path / "a.json").write_text(doc)
    (tmp_path / "b.json").write_text(doc)
    with pytest.raises(ValueError, match="duplicate"):
        FixtureTransport(tmp_path)


def test_cache_outside_rate_limit_makes_hits_free() -> None:
    inner = Scripted("a")
    rate, _, rate_sleeper = limited(inner, budget=20)
    clock = FakeClock(AS_OF)
    stack = CachingTransport(RetryingTransport(rate, POLICY, FakeSleeper()), 60.0, clock)
    for _ in range(5):
        assert stack.post(PAYLOAD) == "a"  # 5 x 20 weight would exceed the budget if charged
    assert rate_sleeper.calls == []
    assert len(inner.calls) == 1


def test_cache_evicts_expired_entries() -> None:
    inner = Scripted(*[f"r{i}" for i in range(4)])
    transport, clock = caching(inner)
    transport.post({"type": "a"})
    transport.post({"type": "b"})
    assert transport.size == 2
    clock.advance(timedelta(seconds=31))
    transport.post({"type": "c"})  # both older entries have expired: dropped, not kept
    assert transport.size == 1

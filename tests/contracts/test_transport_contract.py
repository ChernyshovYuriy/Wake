"""Every Transport (and every decorator stack) must honour the same contract.
A new implementation is covered by adding one entry to BUILDERS."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from hlsignals.core.clock import FakeClock, FakeSleeper
from hlsignals.core.errors import TransportError
from hlsignals.infra.transport import (
    CachingTransport,
    FixtureTransport,
    HttpTransport,
    RateLimitedTransport,
    RetryingTransport,
    RetryPolicy,
    Transport,
    WeightTable,
    canonical_key,
)
from tests.conftest import FIXTURE_DIR, load_fixture
from tests.factories import AS_OF

KNOWN = load_fixture("perp_dexs")
FAILING = load_fixture("error_unknown_type")


def fixture_http() -> HttpTransport:
    """An HttpTransport backed by the captured fixtures instead of the network."""
    replay = FixtureTransport(FIXTURE_DIR)

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            return httpx.Response(200, json=replay.post(json.loads(request.content)))
        except TransportError as exc:
            return httpx.Response(exc.status or 404, text=str(exc))

    return HttpTransport(
        "https://fixture/info", 1.0, httpx.Client(transport=httpx.MockTransport(handler))
    )


def fixtures() -> Transport:
    return FixtureTransport(FIXTURE_DIR)


def retrying(inner: Transport) -> Transport:
    return RetryingTransport(inner, RetryPolicy(2, 0.0, 1.0, 0.0), FakeSleeper())


def rate_limited(inner: Transport) -> Transport:
    clock = FakeClock(AS_OF)
    return RateLimitedTransport(
        inner,
        budget=100,
        window_s=60.0,
        weights=WeightTable(default_base=20),
        clock=clock,
        sleeper=FakeSleeper(clock),
    )


def caching(inner: Transport) -> Transport:
    return CachingTransport(inner, ttl_s=60.0, clock=FakeClock(AS_OF))


BUILDERS: dict[str, Callable[[], Transport]] = {
    "fixture": fixtures,
    "http": fixture_http,
    "retrying": lambda: retrying(fixtures()),
    "rate_limited": lambda: rate_limited(fixtures()),
    "caching": lambda: caching(fixtures()),
    "full_stack": lambda: caching(retrying(rate_limited(fixture_http()))),
}


@pytest.fixture(params=sorted(BUILDERS))
def transport(request: pytest.FixtureRequest) -> Transport:
    return BUILDERS[request.param]()


def test_returns_response_for_known_request(transport: Transport) -> None:
    assert transport.post(KNOWN["request"]) == KNOWN["response"]


def test_repeated_requests_are_equal(transport: Transport) -> None:
    assert transport.post(KNOWN["request"]) == transport.post(KNOWN["request"])


def test_failure_raises_transport_error(transport: Transport) -> None:
    with pytest.raises(TransportError):
        transport.post(FAILING["request"])


def test_payload_not_mutated(transport: Transport) -> None:
    payload: dict[str, Any] = copy.deepcopy(KNOWN["request"])
    transport.post(payload)
    assert canonical_key(payload) == canonical_key(KNOWN["request"])

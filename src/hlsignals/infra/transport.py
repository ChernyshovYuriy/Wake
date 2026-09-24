"""Transport port and its implementations.

``HttpTransport`` talks to the network. Retry, rate limiting and caching are Decorators
around any Transport, stacked by the builder (retry outermost, so every retry attempt is
also charged against the rate budget). ``FixtureTransport`` replays captured responses.
"""

from __future__ import annotations

import copy
import json
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from hlsignals.core.clock import MS_PER_SECOND, Clock, Sleeper, to_ms
from hlsignals.core.errors import (
    AdapterError,
    NonRetryableError,
    RetryableError,
    TransportError,
)

Payload = Mapping[str, Any]
HTTP_OK = 200
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500


def canonical_key(payload: Payload) -> str:
    """Stable identity of a request payload, independent of dict key order."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def error_for_status(status: int, body: str) -> TransportError:
    """Retryable for 429 and 5xx; everything else non-retryable (docs/api-notes.md §8)."""
    message = f"HTTP {status}: {body[:200]}"
    if status == HTTP_TOO_MANY_REQUESTS or status >= HTTP_SERVER_ERROR:
        return RetryableError(message, status=status)
    return NonRetryableError(message, status=status)


class Transport(ABC):
    @abstractmethod
    def post(self, payload: Payload) -> Any:
        """Send one request; return the decoded JSON body or raise TransportError."""


class HttpTransport(Transport):
    def __init__(self, url: str, timeout_s: float, client: httpx.Client | None = None) -> None:
        self.url = url
        self._client = client if client is not None else httpx.Client(timeout=timeout_s)
        self._timeout_s = timeout_s

    def post(self, payload: Payload) -> Any:
        try:
            response = self._client.post(self.url, json=dict(payload), timeout=self._timeout_s)
        except httpx.TransportError as exc:  # timeouts, connection and protocol failures
            raise RetryableError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code != HTTP_OK:
            raise error_for_status(response.status_code, response.text)
        try:
            return response.json()
        except ValueError as exc:
            raise AdapterError(f"response is not valid JSON: {response.text[:200]!r}") from exc


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_delay_s: float
    multiplier: float
    max_delay_s: float

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1: {self.max_attempts}")
        if self.base_delay_s < 0 or self.max_delay_s < 0:
            raise ValueError("retry delays must be non-negative")
        if self.multiplier < 1:
            raise ValueError(f"backoff multiplier must be >= 1: {self.multiplier}")

    def delays(self) -> list[float]:
        """Sleep before each retry (one fewer than max_attempts)."""
        return [
            min(self.base_delay_s * self.multiplier**i, self.max_delay_s)
            for i in range(self.max_attempts - 1)
        ]


class RetryingTransport(Transport):
    """Retries RetryableError with exponential backoff; the single home of retry policy."""

    def __init__(self, inner: Transport, policy: RetryPolicy, sleeper: Sleeper) -> None:
        self._inner = inner
        self._policy = policy
        self._sleeper = sleeper

    def post(self, payload: Payload) -> Any:
        for delay in self._policy.delays():
            try:
                return self._inner.post(payload)
            except RetryableError:
                self._sleeper.sleep(delay)
        return self._inner.post(payload)


@dataclass(frozen=True, slots=True)
class WeightTable:
    """Request weights (docs/api-notes.md §8): a base weight per request type, plus for
    list responses of some types one extra unit per ``items_per_extra_unit`` items."""

    default_base: int
    base: Mapping[str, int] = field(default_factory=dict)
    items_per_extra_unit: Mapping[str, int] = field(default_factory=dict)

    @property
    def max_base(self) -> int:
        return max([self.default_base, *self.base.values()])

    def base_weight(self, payload: Payload) -> int:
        return self.base.get(str(payload.get("type")), self.default_base)

    def extra_weight(self, payload: Payload, response: Any) -> int:
        per_unit = self.items_per_extra_unit.get(str(payload.get("type")))
        if per_unit is None or not isinstance(response, list):
            return 0
        return len(response) // per_unit


class RateLimitedTransport(Transport):
    """Sliding-window weight budget. Base weight is charged before each call (also when
    the call fails); response-size weight is charged after it."""

    def __init__(
        self,
        inner: Transport,
        *,
        budget: int,
        window_s: float,
        weights: WeightTable,
        clock: Clock,
        sleeper: Sleeper,
    ) -> None:
        if weights.max_base > budget:
            raise ValueError(f"a request weight ({weights.max_base}) exceeds the budget ({budget})")
        self._inner = inner
        self._budget = budget
        self._window_s = window_s
        self._weights = weights
        self._clock = clock
        self._sleeper = sleeper
        self._charges: deque[tuple[float, int]] = deque()

    def post(self, payload: Payload) -> Any:
        base = self._weights.base_weight(payload)
        self._wait_for(base)
        self._charge(base)
        response = self._inner.post(payload)
        extra = self._weights.extra_weight(payload, response)
        if extra:
            self._charge(extra)
        return response

    def _now_s(self) -> float:
        return to_ms(self._clock.now()) / MS_PER_SECOND

    def _expire(self, now: float) -> None:
        while self._charges and self._charges[0][0] + self._window_s <= now:
            self._charges.popleft()

    def _wait_for(self, weight: int) -> None:
        """Sleep until enough old charges expire for ``weight`` to fit in the budget."""
        now = self._now_s()
        self._expire(now)
        excess = sum(w for _, w in self._charges) + weight - self._budget
        freed = 0
        release_at = now
        for charged_at, charged in self._charges:
            if freed >= excess:
                break
            freed += charged
            release_at = charged_at + self._window_s
        if release_at > now:
            self._sleeper.sleep(release_at - now)
            self._expire(self._now_s())

    def _charge(self, weight: int) -> None:
        self._charges.append((self._now_s(), weight))


class CachingTransport(Transport):
    """Caches successful responses by canonical payload for ``ttl_s``; errors are not cached."""

    def __init__(self, inner: Transport, ttl_s: float, clock: Clock) -> None:
        if ttl_s <= 0:
            raise ValueError(f"cache ttl must be positive: {ttl_s}")
        self._inner = inner
        self._ttl_ms = ttl_s * MS_PER_SECOND
        self._clock = clock
        self._entries: dict[str, tuple[float, Any]] = {}

    def post(self, payload: Payload) -> Any:
        key = canonical_key(payload)
        now = to_ms(self._clock.now())
        hit = self._entries.get(key)
        if hit is not None and now - hit[0] < self._ttl_ms:
            return copy.deepcopy(hit[1])
        response = self._inner.post(payload)
        self._entries[key] = (now, response)
        return copy.deepcopy(response)


class FixtureTransport(Transport):
    """Replays ``{"request", "response", "status"?}`` files captured in Phase 0."""

    def __init__(self, directory: Path) -> None:
        self._fixtures: dict[str, tuple[int, Any]] = {}
        for path in sorted(directory.glob("*.json")):
            doc = json.loads(path.read_text())
            key = canonical_key(doc["request"])
            if key in self._fixtures:
                raise ValueError(f"duplicate fixture request in {path.name}")
            self._fixtures[key] = (doc.get("status", HTTP_OK), doc["response"])

    def post(self, payload: Payload) -> Any:
        key = canonical_key(payload)
        if key not in self._fixtures:
            raise NonRetryableError(f"no fixture for request {key}")
        status, body = self._fixtures[key]
        if status != HTTP_OK:
            raise error_for_status(status, json.dumps(body))
        return copy.deepcopy(body)

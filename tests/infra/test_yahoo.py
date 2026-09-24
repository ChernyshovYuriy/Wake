from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from hlsignals.core.errors import AdapterError, NonRetryableError, RetryableError
from hlsignals.infra.yahoo import YahooPriceHistory, adapt_yahoo_chart

FIXTURE = Path(__file__).parents[1] / "fixtures" / "prices" / "yahoo_nvda_1d.json"
RAW = json.loads(FIXTURE.read_text())


def test_adapt_golden_fixture() -> None:
    bars = adapt_yahoo_chart(RAW, "NVDA")
    assert len(bars) == 17
    assert bars[0].day == date(2026, 9, 1)  # 13:30 UTC open -> New York date
    assert bars[0].open == pytest.approx(216.75)
    assert bars[0].close == pytest.approx(217.44)
    assert [b.day for b in bars] == sorted(b.day for b in bars)


def test_null_quotes_are_skipped() -> None:
    raw = json.loads(FIXTURE.read_text())
    raw["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None
    assert len(adapt_yahoo_chart(raw, "NVDA")) == 16


def test_not_found_is_empty() -> None:
    raw = {"chart": {"result": None, "error": {"code": "Not Found", "description": "delisted"}}}
    assert adapt_yahoo_chart(raw, "XX") == []


@pytest.mark.parametrize(
    "raw",
    [
        {"chart": {"result": None, "error": {"code": "Bad Request", "description": "x"}}},
        {"nope": 1},
        {"chart": {"result": [{"timestamp": [1], "indicators": {"quote": [{}]}}], "error": None}},
    ],
)
def test_bad_shapes(raw: Any) -> None:
    with pytest.raises(AdapterError):
        adapt_yahoo_chart(raw, "NVDA")


def client(status: int, body: Any, seen: list[httpx.Request] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_daily_bars_request_and_window() -> None:
    seen: list[httpx.Request] = []
    history = YahooPriceHistory(client(200, RAW, seen), timeout_s=5.0)
    bars = history.daily_bars("NVDA", date(2026, 9, 2), date(2026, 9, 3))
    assert [b.day for b in bars] == [date(2026, 9, 2), date(2026, 9, 3)]
    assert seen[0].url.path.endswith("/NVDA")
    assert seen[0].url.params["interval"] == "1d"


@pytest.mark.parametrize(
    ("status", "error"), [(429, RetryableError), (500, RetryableError), (400, NonRetryableError)]
)
def test_http_errors(status: int, error: type[Exception]) -> None:
    with pytest.raises(error):
        YahooPriceHistory(client(status, {}), timeout_s=5.0).daily_bars(
            "NVDA", date(2026, 9, 1), date(2026, 9, 2)
        )


def test_timeout_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    history = YahooPriceHistory(httpx.Client(transport=httpx.MockTransport(handler)), timeout_s=1.0)
    with pytest.raises(RetryableError):
        history.daily_bars("NVDA", date(2026, 9, 1), date(2026, 9, 2))

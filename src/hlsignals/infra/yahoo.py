"""Daily stock bars from Yahoo Finance's chart endpoint (unofficial, undocumented).

Used only to measure backtest outcomes on the real stocks. It sits behind the backtest's
PriceHistory port, so another vendor can replace it. Response shape verified 2026-09-24:
``chart.result[0].timestamp`` holds session opens (UTC seconds); ``indicators.quote[0]``
holds parallel open/close arrays, which may contain nulls (skipped). An unknown ticker
returns ``error.code == "Not Found"`` and yields no bars.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from hlsignals.core.errors import AdapterError, RetryableError
from hlsignals.domain.models import DailyBar
from hlsignals.infra.transport import HTTP_OK, error_for_status

_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_NEW_YORK = ZoneInfo("America/New_York")
_USER_AGENT = "Mozilla/5.0 (hlsignals research backtest)"


def adapt_yahoo_chart(raw: Any, ticker: str) -> list[DailyBar]:
    try:
        chart = raw["chart"]
        error = chart.get("error")
        if error is not None:
            if error.get("code") == "Not Found":
                return []
            raise AdapterError(f"Yahoo error for {ticker}: {error}")
        result = chart["result"][0]
        stamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]
        opens, closes = quote["open"], quote["close"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AdapterError(f"unexpected Yahoo chart shape for {ticker}: {exc}") from exc
    bars = []
    for stamp, o, c in zip(stamps, opens, closes, strict=True):
        if o is None or c is None:
            continue
        day = datetime.fromtimestamp(stamp, UTC).astimezone(_NEW_YORK).date()
        bars.append(DailyBar(ticker, day, float(o), float(c)))
    return sorted(bars, key=lambda b: b.day)


class YahooPriceHistory:
    def __init__(self, client: httpx.Client | None, timeout_s: float) -> None:
        self._client = client or httpx.Client(headers={"User-Agent": _USER_AGENT})
        self._timeout_s = timeout_s

    def daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        """Bars for ``start``..``end`` inclusive (exchange-local dates)."""
        params: dict[str, str | int] = {
            "interval": "1d",
            "period1": int(datetime.combine(start, time(), _NEW_YORK).timestamp()),
            "period2": int(
                datetime.combine(end + timedelta(days=1), time(), _NEW_YORK).timestamp()
            ),
        }
        try:
            response = self._client.get(
                _URL.format(ticker=ticker), params=params, timeout=self._timeout_s
            )
        except httpx.TransportError as exc:
            raise RetryableError(f"Yahoo {ticker}: {type(exc).__name__}: {exc}") from exc
        if response.status_code != HTTP_OK:
            raise error_for_status(response.status_code, response.text)
        return [b for b in adapt_yahoo_chart(response.json(), ticker) if start <= b.day <= end]

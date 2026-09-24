from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from hlsignals.core.clock import FakeSleeper
from hlsignals.core.errors import AdapterError
from hlsignals.domain.models import TapeTrade
from hlsignals.domain.symbols import Symbol
from hlsignals.infra.tape_feed import TapeFeed, TextConnection
from tests.conftest import load_fixture

NVDA = Symbol("xyz", "NVDA")
AAPL = Symbol("xyz", "AAPL")
TRADES = load_fixture("recent_trades_xyz_nvda")["response"]


def message(channel: str, data: Any) -> str:
    return json.dumps({"channel": channel, "data": data})


class FakeConnection:
    """Replays scripted incoming items: str = message, Exception = raised by recv."""

    def __init__(self, incoming: list[str | Exception]) -> None:
        self.incoming = incoming
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def send(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def recv(self, timeout: float) -> str:
        item = self.incoming.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        self.closed = True


class Harness:
    def __init__(self, *connections: FakeConnection, stop_after: int) -> None:
        self.connections = list(connections)
        self.opened: list[FakeConnection] = []
        self.received: list[TapeTrade] = []
        self.sleeper = FakeSleeper()
        self.stop_after = stop_after

    def connect(self, url: str) -> FakeConnection:
        conn = self.connections.pop(0)
        self.opened.append(conn)
        return conn

    def stop(self) -> bool:
        return len(self.received) >= self.stop_after

    def feed(self, symbols: tuple[Symbol, ...] = (NVDA,), **overrides: Any) -> TapeFeed:
        options: dict[str, Any] = {
            "url": "wss://example/ws",
            "symbols": symbols,
            "on_trades": self.received.extend,
            "connect": self.connect,
            "sleeper": self.sleeper,
            "recv_timeout_s": 30.0,
            "reconnect_delay_s": 5.0,
        }
        return TapeFeed(**{**options, **overrides})


def run(harness: Harness, feed_factory: Callable[[], TapeFeed] | None = None) -> None:
    (feed_factory or harness.feed)().run(harness.stop)


def test_subscribes_to_every_symbol_and_forwards_trades() -> None:
    conn = FakeConnection([message("subscriptionResponse", {}), message("trades", TRADES[:2])])
    harness = Harness(conn, stop_after=2)
    run(harness, lambda: harness.feed((NVDA, AAPL)))
    assert conn.sent == [
        {"method": "subscribe", "subscription": {"type": "trades", "coin": "xyz:NVDA"}},
        {"method": "subscribe", "subscription": {"type": "trades", "coin": "xyz:AAPL"}},
    ]
    assert [t.tid for t in harness.received] == [t["tid"] for t in TRADES[:2]]
    assert conn.closed


def test_pings_when_quiet_and_accepts_pong() -> None:
    conn = FakeConnection([TimeoutError(), message("pong", None), message("trades", TRADES[:1])])
    harness = Harness(conn, stop_after=1)
    run(harness)
    assert {"method": "ping"} in conn.sent


def test_reconnects_after_connection_loss_and_resubscribes() -> None:
    dropped = FakeConnection([ConnectionResetError("reset")])
    fresh = FakeConnection([message("trades", TRADES[:1])])
    harness = Harness(dropped, fresh, stop_after=1)
    run(harness)
    assert harness.sleeper.calls == [5.0]
    assert dropped.closed
    assert fresh.sent[0]["method"] == "subscribe"


def test_error_channel_is_logged_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    conn = FakeConnection([message("error", "bad subscription"), message("trades", TRADES[:1])])
    harness = Harness(conn, stop_after=1)
    run(harness)
    assert "bad subscription" in caplog.text


def test_unknown_channel_fails_loudly() -> None:
    conn = FakeConnection([message("mystery", {})])
    harness = Harness(conn, stop_after=1)
    with pytest.raises(AdapterError, match="mystery"):
        run(harness)
    assert conn.closed


def test_already_stopped_never_connects() -> None:
    harness = Harness(FakeConnection([]), stop_after=0)
    run(harness)
    assert harness.opened == []


class FakeClientConnection:
    def __init__(self, frame: str | bytes) -> None:
        self.frame = frame
        self.sent: list[str] = []
        self.timeouts: list[float | None] = []

    def send(self, text: str) -> None:
        self.sent.append(text)

    def recv(self, timeout: float | None = None) -> str | bytes:
        self.timeouts.append(timeout)
        return self.frame


@pytest.mark.parametrize("frame", ['{"a": 1}', b'{"a": 1}'])
def test_text_connection_decodes_frames(frame: str | bytes) -> None:
    raw = FakeClientConnection(frame)
    conn = TextConnection(raw)  # type: ignore[arg-type]
    conn.send("hello")
    assert conn.recv(timeout=3.0) == '{"a": 1}'
    assert raw.sent == ["hello"]
    assert raw.timeouts == [3.0]

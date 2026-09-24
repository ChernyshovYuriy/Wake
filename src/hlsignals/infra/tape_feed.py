"""Websocket tape feed: subscribes to ``trades`` per symbol (docs/api-notes.md §9).

There is no all-fills channel, so each tracked symbol gets its own subscription. When no
message arrives within ``recv_timeout_s`` the feed pings so the server keeps the
connection open. A dropped connection is reopened after ``reconnect_delay_s`` and every
symbol is subscribed again; trades replayed on reconnect are deduped downstream by the
census recorder.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection, connect

from hlsignals.core.clock import Sleeper
from hlsignals.core.errors import AdapterError
from hlsignals.domain.models import TapeTrade
from hlsignals.domain.symbols import Symbol
from hlsignals.infra.adapters import adapt_tape_trades

logger = logging.getLogger(__name__)
_IGNORED_CHANNELS = frozenset({"subscriptionResponse", "pong"})


class WsConnection(Protocol):
    def send(self, text: str) -> None: ...

    def recv(self, timeout: float) -> str: ...


class TextConnection:
    """Adapts a websockets ClientConnection to WsConnection (frames decoded to text)."""

    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws

    def send(self, text: str) -> None:
        self._ws.send(text)

    def recv(self, timeout: float) -> str:
        frame = self._ws.recv(timeout=timeout)
        return frame if isinstance(frame, str) else frame.decode()


@contextmanager
def websocket_connect(url: str) -> Iterator[WsConnection]:
    """The real connector for TapeFeed (network I/O; exercised by the live tests)."""
    with connect(url) as ws:
        yield TextConnection(ws)


class TapeFeed:
    def __init__(
        self,
        *,
        url: str,
        symbols: Sequence[Symbol],
        on_trades: Callable[[list[TapeTrade]], None],
        connect: Callable[[str], AbstractContextManager[WsConnection]],
        sleeper: Sleeper,
        recv_timeout_s: float,
        reconnect_delay_s: float,
    ) -> None:
        self._url = url
        self._symbols = tuple(symbols)
        self._on_trades = on_trades
        self._connect = connect
        self._sleeper = sleeper
        self._recv_timeout_s = recv_timeout_s
        self._reconnect_delay_s = reconnect_delay_s

    def run(self, stop: Callable[[], bool]) -> None:
        """Stream until ``stop()`` returns True. AdapterError (unknown shapes) propagates."""
        while not stop():
            try:
                with self._connect(self._url) as conn:
                    self._subscribe(conn)
                    self._pump(conn, stop)
            except (ConnectionClosed, OSError) as exc:
                logger.warning("tape connection lost (%s); reconnecting", exc)
                self._sleeper.sleep(self._reconnect_delay_s)

    def _subscribe(self, conn: WsConnection) -> None:
        for symbol in self._symbols:
            sub = {"type": "trades", "coin": str(symbol)}
            conn.send(json.dumps({"method": "subscribe", "subscription": sub}))

    def _pump(self, conn: WsConnection, stop: Callable[[], bool]) -> None:
        while not stop():
            try:
                raw = conn.recv(timeout=self._recv_timeout_s)
            except TimeoutError:
                conn.send(json.dumps({"method": "ping"}))
                continue
            self._dispatch(json.loads(raw))

    def _dispatch(self, message: dict[str, object]) -> None:
        channel = message.get("channel")
        if channel == "trades":
            self._on_trades(adapt_tape_trades(message.get("data")))
        elif channel == "error":
            logger.error("tape feed error from server: %s", message.get("data"))
        elif channel not in _IGNORED_CHANNELS:
            raise AdapterError(f"unknown websocket channel {channel!r}")

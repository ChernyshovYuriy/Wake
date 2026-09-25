"""Rule 8: unit tests never reach the network. tests/conftest.py blocks it; `live` tests opt out."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import httpx
import pytest

from tests.conftest import GUARDS, NetworkBlockedError, network_guards

TEST_NET = ("192.0.2.1", 443)  # RFC 5737 documentation address: never routable


def test_raw_internet_connection_is_blocked() -> None:
    with pytest.raises(NetworkBlockedError, match=r"192\.0\.2\.1"):
        socket.create_connection(TEST_NET, timeout=1)


def test_connect_ex_is_blocked() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(NetworkBlockedError),
    ):
        sock.connect_ex(TEST_NET)


def test_ipv6_is_blocked() -> None:
    with (
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock,
        pytest.raises(NetworkBlockedError),
    ):
        sock.connect(("2001:db8::1", 443, 0, 0))


def test_http_client_is_blocked() -> None:
    with pytest.raises(NetworkBlockedError):
        httpx.get(f"http://{TEST_NET[0]}/", timeout=1)


def test_asyncio_connection_is_blocked() -> None:
    async def connect() -> None:
        await asyncio.open_connection(*TEST_NET)

    with pytest.raises(NetworkBlockedError):
        asyncio.run(connect())


def test_local_socket_pairs_still_work() -> None:
    """asyncio's event loop and other in-process plumbing use AF_UNIX pairs: not network."""
    a, b = socket.socketpair()
    with a, b:
        a.sendall(b"ping")
        assert b.recv(4) == b"ping"


def test_name_resolution_is_blocked() -> None:
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("api.hyperliquid.xyz", 443)


def test_local_unix_socket_connections_still_work(tmp_path: Path) -> None:
    path = str(tmp_path / "s")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(path)
        server.listen()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(path)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            assert client.connect_ex(path) == 0


class _Node:
    def __init__(self, live: bool) -> None:
        self.live = live

    def get_closest_marker(self, name: str) -> object | None:
        return object() if self.live and name == "live" else None


def test_live_tests_are_exempt_and_others_are_guarded(request: pytest.FixtureRequest) -> None:
    assert network_guards(request.node) == GUARDS
    assert network_guards(_Node(live=True)) == ()  # type: ignore[arg-type]
    assert network_guards(_Node(live=False)) == GUARDS  # type: ignore[arg-type]

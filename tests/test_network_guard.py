"""Rule 8: unit tests never reach the network. tests/conftest.py blocks it; `live` tests opt out."""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from tests.conftest import NetworkBlockedError

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

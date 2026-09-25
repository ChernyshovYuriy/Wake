from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hl"


def load_fixture(name: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((FIXTURE_DIR / f"{name}.json").read_text())
    return doc


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR


# --- rule 8: no network in unit tests -----------------------------------------------------------

_INTERNET = (socket.AF_INET, socket.AF_INET6)
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


class NetworkBlockedError(RuntimeError):
    """Not an OSError on purpose: HTTP clients and retry loops catch OSError, and must not turn a
    forbidden connection into a retry or a handled error."""


def _blocked(what: object) -> NetworkBlockedError:
    return NetworkBlockedError(
        f"unit tests must not use the network ({what!r}); use FixtureTransport, or mark the "
        "test @pytest.mark.live"
    )


def _guarded_connect(self: socket.socket, address: object) -> None:
    if self.family in _INTERNET:
        raise _blocked(address)
    _real_connect(self, address)  # type: ignore[arg-type]


def _guarded_connect_ex(self: socket.socket, address: object) -> int:
    if self.family in _INTERNET:
        raise _blocked(address)
    return _real_connect_ex(self, address)  # type: ignore[arg-type]


def _guarded_getaddrinfo(host: object, *args: object, **kwargs: object) -> list[object]:
    raise _blocked(host)  # name resolution is network I/O too


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test except @pytest.mark.live runs with internet sockets and DNS blocked.
    Local AF_UNIX sockets (asyncio's self-pipe, socketpair) keep working."""
    if request.node.get_closest_marker("live") is None:
        monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
        monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
        monkeypatch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)

"""WalletRegistry port: every wallet the census has seen trading a tracked symbol."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WalletObservation:
    address: str
    first_seen_ms: int
    last_seen_ms: int
    n_fills: int


class WalletRegistry(Protocol):
    def observe(self, addresses: Iterable[str], time_ms: int) -> None:
        """Record one trade for each distinct address."""
        ...

    def get(self, address: str) -> WalletObservation | None: ...

    def observations(self, min_fills: int) -> list[WalletObservation]:
        """Wallets with at least ``min_fills`` observed trades, sorted by address."""
        ...

    def close(self) -> None: ...


class InMemoryRegistry:
    def __init__(self) -> None:
        self._rows: dict[str, WalletObservation] = {}

    def observe(self, addresses: Iterable[str], time_ms: int) -> None:
        for address in set(addresses):
            old = self._rows.get(address)
            self._rows[address] = (
                WalletObservation(address, time_ms, time_ms, 1)
                if old is None
                else WalletObservation(
                    address,
                    min(old.first_seen_ms, time_ms),
                    max(old.last_seen_ms, time_ms),
                    old.n_fills + 1,
                )
            )

    def get(self, address: str) -> WalletObservation | None:
        return self._rows.get(address)

    def observations(self, min_fills: int) -> list[WalletObservation]:
        return sorted(
            (o for o in self._rows.values() if o.n_fills >= min_fills), key=lambda o: o.address
        )

    def close(self) -> None:
        """Nothing to release."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    first_seen_ms INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL,
    n_fills INTEGER NOT NULL
)
"""
_UPSERT = """
INSERT INTO wallets (address, first_seen_ms, last_seen_ms, n_fills) VALUES (?, ?, ?, 1)
ON CONFLICT(address) DO UPDATE SET
    first_seen_ms = min(first_seen_ms, excluded.first_seen_ms),
    last_seen_ms = max(last_seen_ms, excluded.last_seen_ms),
    n_fills = n_fills + 1
"""
_COLUMNS = "address, first_seen_ms, last_seen_ms, n_fills"


_BUSY_TIMEOUT_S = 30.0  # the census writes while discovery / runs read


class SqliteRegistry:
    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_S)
        self._db.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
        self._db.execute(_SCHEMA)
        self._db.commit()

    def observe(self, addresses: Iterable[str], time_ms: int) -> None:
        with self._db:
            self._db.executemany(_UPSERT, [(a, time_ms, time_ms) for a in set(addresses)])

    def get(self, address: str) -> WalletObservation | None:
        row = self._db.execute(
            f"SELECT {_COLUMNS} FROM wallets WHERE address = ?",
            (address,),
        ).fetchone()
        return None if row is None else WalletObservation(*row)

    def observations(self, min_fills: int) -> list[WalletObservation]:
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM wallets WHERE n_fills >= ? ORDER BY address",
            (min_fills,),
        ).fetchall()
        return [WalletObservation(*row) for row in rows]

    def close(self) -> None:
        self._db.close()

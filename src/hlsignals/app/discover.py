"""Discovery: turn the census into a shortlist of wallets worth following.

The census records thousands of wallets; vetting one can take many API calls, so the daily
run cannot vet them all. Discovery (a weekly job) vets them incrementally: never-vetted
wallets first (most active first), accepted wallets again every run (so a wallet that
degrades drops out), rejected ones only after ``revet_days``. Accepted wallets are written
to a curated-format wallets file that the daily run reads as a source.

A wallet whose vetting hit an API error is not recorded, so it is simply retried next run.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from hlsignals.app.config import DiscoverySettings
from hlsignals.app.vet import VetResult
from hlsignals.core.clock import MS_PER_DAY, from_ms, to_ms
from hlsignals.domain.models import WalletRecord
from hlsignals.wallets.census.registry import WalletObservation, WalletRegistry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vetted (
    address TEXT PRIMARY KEY,
    vetted_ms INTEGER NOT NULL,
    accepted INTEGER NOT NULL,
    trust REAL,
    rejected_by TEXT,
    reason TEXT NOT NULL
)
"""
_BUSY_TIMEOUT_S = 30.0


@dataclass(frozen=True, slots=True)
class VettedRow:
    address: str
    vetted_ms: int
    accepted: bool
    trust: float | None
    rejected_by: str | None  # filter name when rejected
    reason: str


@dataclass(frozen=True, slots=True)
class ShortlistEntry:
    address: str
    trust: float
    vetted_ms: int


class DiscoveryStore:
    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_S)
        self._db.execute(_SCHEMA)
        self._db.commit()

    def record(self, result: VetResult, vetted_ms: int) -> None:
        if result.error is not None:
            return
        trust = result.scored.trust if result.scored else None
        rejected_by, reason = result.rejection or (None, "accepted")
        with self._db:
            self._db.execute(
                "INSERT INTO vetted VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(address) DO UPDATE SET "
                "vetted_ms = excluded.vetted_ms, accepted = excluded.accepted, "
                "trust = excluded.trust, rejected_by = excluded.rejected_by, "
                "reason = excluded.reason",
                (
                    result.record.address,
                    vetted_ms,
                    int(result.accepted),
                    trust,
                    rejected_by,
                    reason,
                ),
            )

    def last(self, address: str) -> tuple[int, bool] | None:
        row = self._db.execute(
            "SELECT vetted_ms, accepted FROM vetted WHERE address = ?", (address,)
        ).fetchone()
        return None if row is None else (row[0], bool(row[1]))

    def shortlist(self) -> list[ShortlistEntry]:
        rows = self._db.execute(
            "SELECT address, trust, vetted_ms FROM vetted WHERE accepted = 1 "
            "ORDER BY trust DESC, address"
        ).fetchall()
        return [ShortlistEntry(*row) for row in rows]

    def recent(self, limit: int) -> list[VettedRow]:
        rows = self._db.execute(
            "SELECT address, vetted_ms, accepted, trust, rejected_by, reason FROM vetted "
            "ORDER BY vetted_ms DESC, address LIMIT ?",
            (limit,),
        ).fetchall()
        return [VettedRow(a, t, bool(ok), trust, by, why) for a, t, ok, trust, by, why in rows]

    def rejection_counts(self) -> dict[str, int]:
        """Currently rejected wallets by the filter that rejected them."""
        rows = self._db.execute(
            "SELECT rejected_by, count(*) FROM vetted WHERE accepted = 0 GROUP BY rejected_by"
        ).fetchall()
        return {name: n for name, n in rows}

    def last_vetted_ms(self) -> int | None:
        value: int | None = self._db.execute("SELECT max(vetted_ms) FROM vetted").fetchone()[0]
        return value

    def close(self) -> None:
        self._db.close()


def select_candidates(
    observations: Sequence[WalletObservation],
    store: DiscoveryStore,
    now_ms: int,
    settings: DiscoverySettings,
) -> list[WalletRecord]:
    revet_ms = settings.revet_days * MS_PER_DAY
    fresh: list[WalletObservation] = []
    recheck: list[WalletObservation] = []
    due: list[WalletObservation] = []
    for o in observations:
        if o.n_fills < settings.min_observations:
            continue
        last = store.last(o.address)
        if last is None:
            fresh.append(o)
        elif last[1]:
            recheck.append(o)
        elif now_ms - last[0] >= revet_ms:
            due.append(o)
    fresh.sort(key=lambda o: (-o.n_fills, o.address))
    ordered = [*fresh, *recheck, *due][: settings.max_wallets_per_run]
    return [
        WalletRecord(o.address, "census", None, f"census_fills={o.n_fills}", from_ms(now_ms))
        for o in ordered
    ]


class Vetting(Protocol):
    def vet(self, records: Sequence[WalletRecord], as_of: datetime) -> list[VetResult]: ...


@dataclass(frozen=True, slots=True)
class DiscoverySummary:
    candidates: int
    skipped_recent: int
    vetted: int
    accepted: int
    rejected: dict[str, int]
    errors: int
    shortlisted: int


def discover(
    *,
    pipeline: Vetting,
    registry: WalletRegistry,
    store: DiscoveryStore,
    settings: DiscoverySettings,
    as_of: datetime,
    shortlist_path: Path,
) -> DiscoverySummary:
    now_ms = to_ms(as_of)
    observations = registry.observations(min_fills=settings.min_observations)
    candidates = select_candidates(observations, store, now_ms, settings)
    results = pipeline.vet(candidates, as_of)
    for result in results:
        store.record(result, now_ms)
    shortlist = store.shortlist()
    write_shortlist(shortlist_path, shortlist, as_of)
    rejected = Counter(r.rejection[0] for r in results if r.rejection is not None)
    return DiscoverySummary(
        candidates=len(observations),
        skipped_recent=len(observations) - len(candidates),
        vetted=len(results),
        accepted=sum(1 for r in results if r.accepted),
        rejected=dict(rejected),
        errors=sum(1 for r in results if r.error is not None),
        shortlisted=len(shortlist),
    )


def write_shortlist(path: Path, entries: Sequence[ShortlistEntry], as_of: datetime) -> None:
    """A curated-format wallets file. Trust is recorded in the note, not as ``score``:
    it is this system's own output, not an external prior."""
    lines = [
        f"# Wallets accepted by discovery (generated {as_of.isoformat()}; do not edit).",
        "# Re-vetted every discovery run; wallets that stop passing are removed.",
        "",
    ]
    for entry in entries:
        lines += [
            "[[wallets]]",
            f'address = "{entry.address}"',
            f'note = "trust {entry.trust:.3f} when vetted"',
            "",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines))
    tmp.replace(path)  # atomic: the daily run never reads a half-written file

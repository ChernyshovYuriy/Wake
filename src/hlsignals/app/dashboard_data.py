"""Read-only views of the files and databases the scheduled jobs write, for the dashboard.

Nothing here writes or creates anything; a missing file is an empty view, not an error.
"""

from __future__ import annotations

import json
import re
import sqlite3
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from hlsignals.app.config import Settings
from hlsignals.app.discover import DiscoveryStore, ShortlistEntry, VettedRow
from hlsignals.core.clock import MS_PER_DAY, MS_PER_SECOND, from_ms
from hlsignals.domain.models import SignalReport
from hlsignals.reporting.renderers import report_from_json
from hlsignals.wallets.census.registry import SqliteRegistry, WalletObservation

_DATED = re.compile(r"^(signals|backtest)-(\d{4}-\d{2}-\d{2})\.json$")
_NEW_WALLET_DAYS = 14
_TOP_WALLETS = 15
_SECONDS_PER_MINUTE = 60


@dataclass(frozen=True, slots=True)
class DashboardPaths:
    reports_dir: Path
    census_db: Path
    discovery_db: Path
    curated: Path

    @classmethod
    def from_settings(cls, settings: Settings, base: Path) -> DashboardPaths:
        curated = next(
            (
                s["path"]
                for s in settings.wallet_sources.sources
                if s.get("type") == "curated" and s.get("name", "curated") == "curated"
            ),
            "config/wallets.toml",
        )
        return cls(
            reports_dir=base / settings.dashboard.reports_dir,
            census_db=base / settings.census.db_path,
            discovery_db=base / settings.discovery.state_path,
            curated=base / curated,
        )


def _days(paths: DashboardPaths, kind: str) -> list[date]:
    if not paths.reports_dir.is_dir():
        return []
    found = [m for f in paths.reports_dir.iterdir() if (m := _DATED.match(f.name))]
    return sorted((date.fromisoformat(m[2]) for m in found if m[1] == kind), reverse=True)


def _report_file(paths: DashboardPaths, kind: str, day: date | None) -> Path:
    return paths.reports_dir / f"{kind}-{'latest' if day is None else day.isoformat()}.json"


def signal_report_days(paths: DashboardPaths) -> list[date]:
    return _days(paths, "signals")


def load_signal_report(paths: DashboardPaths, day: date | None) -> SignalReport | None:
    """The report saved for ``day`` (None: the latest), or None if there is none."""
    path = _report_file(paths, "signals", day)
    return report_from_json(path.read_text()) if path.is_file() else None


def backtest_days(paths: DashboardPaths) -> list[date]:
    return _days(paths, "backtest")


def load_backtest(paths: DashboardPaths, day: date | None) -> dict[str, Any] | None:
    path = _report_file(paths, "backtest", day)
    if not path.is_file():
        return None
    doc: dict[str, Any] = json.loads(path.read_text())
    return doc


@dataclass(frozen=True, slots=True)
class CensusStats:
    wallets: int
    qualified: int  # wallets with at least min_observations trades
    trades: int  # sum of observed trades (a trade counts for both sides)
    last_seen_ms: int | None
    minutes_since_last: float | None
    live: bool
    top: list[WalletObservation]
    new_per_day: list[tuple[date, int]]  # wallets first seen per day, recent days


def census_stats(
    paths: DashboardPaths, now_ms: int, *, min_observations: int, live_minutes: float
) -> CensusStats | None:
    if not paths.census_db.is_file():
        return None
    registry = SqliteRegistry(paths.census_db, read_only=True)
    try:
        rows = registry.observations(min_fills=0)
    except sqlite3.Error:
        return None
    finally:
        registry.close()
    last = max((o.last_seen_ms for o in rows), default=None)
    minutes = None if last is None else (now_ms - last) / MS_PER_SECOND / _SECONDS_PER_MINUTE
    since = from_ms(now_ms).date() - timedelta(days=_NEW_WALLET_DAYS - 1)
    first_days = Counter(from_ms(o.first_seen_ms).date() for o in rows)
    new = [
        (d, first_days.get(d, 0))
        for d in (since + timedelta(days=i) for i in range(_NEW_WALLET_DAYS))
    ]
    return CensusStats(
        wallets=len(rows),
        qualified=sum(1 for o in rows if o.n_fills >= min_observations),
        trades=sum(o.n_fills for o in rows),
        last_seen_ms=last,
        minutes_since_last=minutes,
        live=minutes is not None and minutes <= live_minutes,
        top=sorted(rows, key=lambda o: (-o.n_fills, o.address))[:_TOP_WALLETS],
        new_per_day=new,
    )


@dataclass(frozen=True, slots=True)
class DiscoveryState:
    shortlist: list[ShortlistEntry]
    rejected: dict[str, int]
    last_run_ms: int | None
    recent: list[VettedRow]


def discovery_state(paths: DashboardPaths, *, limit: int) -> DiscoveryState | None:
    if not paths.discovery_db.is_file():
        return None
    store = DiscoveryStore(paths.discovery_db)
    try:
        return DiscoveryState(
            shortlist=store.shortlist(),
            rejected=store.rejection_counts(),
            last_run_ms=store.last_vetted_ms(),
            recent=store.recent(limit),
        )
    finally:
        store.close()


def curated_wallets(paths: DashboardPaths) -> tuple[list[tuple[str, str]], str | None]:
    """(address, note) pairs from your curated file, or an error message to show."""
    if not paths.curated.is_file():
        return [], None
    try:
        doc = tomllib.loads(paths.curated.read_text())
    except tomllib.TOMLDecodeError as exc:
        return [], f"{paths.curated} is not valid TOML: {exc}"
    wallets = doc.get("wallets", [])
    return [(str(w.get("address", "?")), str(w.get("note", ""))) for w in wallets], None


def age_days(ms: int | None, now_ms: int) -> float | None:
    return None if ms is None else (now_ms - ms) / MS_PER_DAY

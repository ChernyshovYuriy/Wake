from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from hlsignals.app.dashboard_data import (
    DashboardPaths,
    backtest_days,
    census_stats,
    curated_wallets,
    discovery_state,
    load_backtest,
    load_signal_report,
    signal_report_days,
)
from hlsignals.app.discover import DiscoveryStore
from hlsignals.app.system_status import unit_statuses
from hlsignals.app.vet import VetResult
from hlsignals.core.clock import MS_PER_DAY, MS_PER_HOUR, to_ms
from hlsignals.reporting.renderers import JsonRenderer
from hlsignals.wallets.census.registry import SqliteRegistry
from tests.factories import AS_OF, make_scored_wallet, make_wallet_record, wallet_address
from tests.reporting.sample import sample_report

NOW_MS = to_ms(AS_OF)


def paths(tmp: Path) -> DashboardPaths:
    return DashboardPaths(
        reports_dir=tmp / "reports",
        census_db=tmp / "census.sqlite",
        discovery_db=tmp / "discovery.sqlite",
        curated=tmp / "wallets.toml",
    )


def test_everything_missing_is_empty_not_an_error(tmp_path: Path) -> None:
    p = paths(tmp_path)
    assert load_signal_report(p, None) is None
    assert signal_report_days(p) == []
    assert load_backtest(p, None) is None
    assert backtest_days(p) == []
    assert census_stats(p, NOW_MS, min_observations=20, live_minutes=30) is None
    assert discovery_state(p, limit=10) is None
    assert curated_wallets(p) == ([], None)
    assert not (tmp_path / "census.sqlite").exists()  # reading never creates databases


def test_signal_reports_latest_and_by_day(tmp_path: Path) -> None:
    p = paths(tmp_path)
    p.reports_dir.mkdir()
    text = JsonRenderer().render(sample_report())
    for name in ("signals-2026-09-23.json", "signals-2026-09-24.json", "signals-latest.json"):
        (p.reports_dir / name).write_text(text)
    assert signal_report_days(p) == [date(2026, 9, 24), date(2026, 9, 23)]
    latest = load_signal_report(p, None)
    assert latest is not None
    assert latest.signals[0].symbol.coin == "NVDA"
    assert load_signal_report(p, date(2026, 9, 23)) is not None
    assert load_signal_report(p, date(2026, 1, 1)) is None


def test_backtest_docs(tmp_path: Path) -> None:
    p = paths(tmp_path)
    p.reports_dir.mkdir()
    doc = {"verdict": "INCONCLUSIVE: 0 trades < 30 needed for a verdict"}
    (p.reports_dir / "backtest-2026-09-20.json").write_text(json.dumps(doc))
    (p.reports_dir / "backtest-latest.json").write_text(json.dumps(doc))
    assert backtest_days(p) == [date(2026, 9, 20)]
    loaded = load_backtest(p, None)
    assert loaded is not None
    assert loaded["verdict"].startswith("INCONCLUSIVE")


def test_census_stats(tmp_path: Path) -> None:
    p = paths(tmp_path)
    registry = SqliteRegistry(p.census_db)
    busy, quiet, old = (wallet_address(i) for i in (1, 2, 3))
    for i in range(25):
        registry.observe([busy], NOW_MS - 5 * 60_000 - i)
    registry.observe([quiet], NOW_MS - 2 * MS_PER_DAY)
    registry.observe([old], NOW_MS - 20 * MS_PER_DAY)
    registry.close()
    stats = census_stats(p, NOW_MS, min_observations=20, live_minutes=30)
    assert stats is not None
    assert (stats.wallets, stats.qualified, stats.trades) == (3, 1, 27)
    assert stats.live is True  # last trade 5 minutes ago
    assert stats.top[0].address == busy
    assert (
        sum(n for _, n in stats.new_per_day) == 2
    )  # busy and quiet first seen in the last 14 days
    stale = census_stats(p, NOW_MS + MS_PER_HOUR, min_observations=20, live_minutes=30)
    assert stale is not None
    assert stale.live is False


def test_discovery_state_and_curated(tmp_path: Path) -> None:
    p = paths(tmp_path)
    store = DiscoveryStore(p.discovery_db)
    good, bad = wallet_address(1), wallet_address(2)
    store.record(
        VetResult(
            make_wallet_record(address=good),
            make_scored_wallet(address=good, trust=0.62),
            None,
            None,
        ),
        NOW_MS,
    )
    store.record(
        VetResult(make_wallet_record(address=bad), None, ("maker_profile", "prescreen"), None),
        NOW_MS - 1,
    )
    store.close()
    state = discovery_state(p, limit=10)
    assert state is not None
    assert [s.address for s in state.shortlist] == [good]
    assert state.rejected == {"maker_profile": 1}
    assert state.last_run_ms == NOW_MS
    assert [r.address for r in state.recent] == [good, bad]

    p.curated.write_text(f'[[wallets]]\naddress = "{good}"\nnote = "from Hyperdash"\n')
    wallets, error = curated_wallets(p)
    assert error is None
    assert wallets == [(good, "from Hyperdash")]
    p.curated.write_text("[[wallets]\n")
    assert curated_wallets(p)[1] is not None  # a broken file is reported, not raised


SHOW = """Id=hlsignals-census.service
LoadState=loaded
ActiveState=active
SubState=running
Result=success
ActiveEnterTimestamp=Thu 2026-09-24 08:00:00 EDT

Id=hlsignals-run.timer
LoadState=loaded
ActiveState=active
SubState=waiting
Result=success
NextElapseUSecRealtime=Fri 2026-09-25 08:45:00 EDT
LastTriggerUSec=Thu 2026-09-24 08:45:00 EDT

Id=hlsignals-backtest.timer
LoadState=not-found
ActiveState=inactive
SubState=dead
Result=success
"""


def test_unit_statuses_parse_systemctl_show() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        calls.append(argv)
        return SHOW

    units = ["hlsignals-census.service", "hlsignals-run.timer", "hlsignals-backtest.timer"]
    statuses = {s.unit: s for s in unit_statuses(units, runner)}
    assert calls[0][:2] == ["systemctl", "show"]
    census = statuses["hlsignals-census.service"]
    assert (census.state, census.healthy, census.since) == (
        "active (running)",
        True,
        "Thu 2026-09-24 08:00:00 EDT",
    )
    timer = statuses["hlsignals-run.timer"]
    assert timer.next_run == "Fri 2026-09-25 08:45:00 EDT"
    assert timer.last_run == "Thu 2026-09-24 08:45:00 EDT"
    missing = statuses["hlsignals-backtest.timer"]
    assert missing.state == "not installed"
    assert missing.healthy is False


def test_unit_statuses_without_systemd() -> None:
    def runner(argv: list[str]) -> str:
        raise FileNotFoundError("systemctl")

    assert unit_statuses(["hlsignals-census.service"], runner) == []


def test_failed_unit_is_unhealthy() -> None:
    show = (
        "Id=hlsignals-discover.service\nLoadState=loaded\nActiveState=failed\n"
        "SubState=failed\nResult=exit-code\n"
    )
    (status,) = unit_statuses(["hlsignals-discover.service"], lambda argv: show)
    assert status.healthy is False
    assert status.state == "failed (failed), result exit-code"

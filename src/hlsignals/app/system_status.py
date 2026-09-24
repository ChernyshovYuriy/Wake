"""systemd state of the hl-whale-signals units, read with ``systemctl show``.

The command runner is injected so the parsing is testable anywhere; on a machine without
systemd (or without these units) the dashboard simply shows nothing / "not installed".
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

Runner = Callable[[list[str]], str]

UNITS = (
    "hlsignals-census.service",
    "hlsignals-dashboard.service",
    "hlsignals-run.timer",
    "hlsignals-run.service",
    "hlsignals-discover.timer",
    "hlsignals-discover.service",
    "hlsignals-backtest.timer",
    "hlsignals-backtest.service",
)
_PROPERTIES = (
    "Id,LoadState,ActiveState,SubState,Result,ActiveEnterTimestamp,"
    "NextElapseUSecRealtime,LastTriggerUSec"
)
_TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class UnitStatus:
    unit: str
    state: str
    healthy: bool
    since: str | None
    next_run: str | None
    last_run: str | None


def run_systemctl(argv: list[str]) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT_S, check=False)
    return done.stdout


def unit_statuses(units: Sequence[str], runner: Runner) -> list[UnitStatus]:
    try:
        output = runner(["systemctl", "show", *units, f"--property={_PROPERTIES}"])
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    return [_status(block) for block in output.strip().split("\n\n") if block.strip()]


def _status(block: str) -> UnitStatus:
    props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
    unit = props.get("Id", "?")
    if props.get("LoadState") != "loaded":
        return UnitStatus(unit, "not installed", False, None, None, None)
    active, sub, result = (
        props.get("ActiveState", ""),
        props.get("SubState", ""),
        props.get("Result", ""),
    )
    state = f"{active} ({sub})" + ("" if result in ("", "success") else f", result {result}")
    # A oneshot job that finished cleanly is "inactive (dead)" between runs: that is healthy.
    healthy = active != "failed" and result in ("", "success")
    return UnitStatus(
        unit=unit,
        state=state,
        healthy=healthy,
        since=props.get("ActiveEnterTimestamp") or None,
        next_run=props.get("NextElapseUSecRealtime") or None,
        last_run=props.get("LastTriggerUSec") or None,
    )

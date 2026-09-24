"""The systemd units must keep matching the CLI and config they invoke."""

from __future__ import annotations

import configparser
import shlex
from pathlib import Path

import pytest

from hlsignals.app.cli import _parser
from hlsignals.app.config import load_settings
from hlsignals.app.wiring import validate_settings

ROOT = Path(__file__).parents[2]
SYSTEM = ROOT / "system"
SERVICES = sorted(SYSTEM.glob("hlsignals-*.service"))
TIMERS = sorted(SYSTEM.glob("hlsignals-*.timer"))
APP_DIR = "@APP_DIR@"


def unit(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(path.read_text())
    return parser


def test_expected_units_exist() -> None:
    assert [p.name for p in SERVICES] == [
        "hlsignals-backtest.service",
        "hlsignals-census.service",
        "hlsignals-discover.service",
        "hlsignals-run.service",
    ]
    assert {p.stem for p in TIMERS} == {"hlsignals-backtest", "hlsignals-discover", "hlsignals-run"}


@pytest.mark.parametrize("path", SERVICES, ids=lambda p: p.name)
def test_exec_start_is_a_valid_cli_invocation(path: Path) -> None:
    service = unit(path)["Service"]
    argv = shlex.split(service["ExecStart"])
    assert argv[0] == f"{APP_DIR}/.venv/bin/hlsignals"
    assert service["WorkingDirectory"] == APP_DIR
    assert service["User"] == "@USER@"
    args = [a.replace(f"{APP_DIR}/", "") for a in argv[1:]]
    parsed = _parser().parse_args(args)  # raises SystemExit on a stale command or flag
    assert parsed.config == Path("config/pi.toml")


@pytest.mark.parametrize("path", TIMERS, ids=lambda p: p.name)
def test_timers_point_at_their_service_in_new_york_time(path: Path) -> None:
    timer = unit(path)["Timer"]
    assert timer["Unit"] == f"{path.stem}.service"
    assert timer["OnCalendar"].endswith("America/New_York")


def test_pi_config_is_valid_and_follows_the_shortlist() -> None:
    settings = load_settings(ROOT / "config" / "pi.toml")
    validate_settings(settings)
    names = [spec.get("name", spec["type"]) for spec in settings.wallet_sources.sources]
    assert names == ["curated", "discovered"]
    shortlist = next(s for s in settings.wallet_sources.sources if s.get("name") == "discovered")
    assert shortlist["path"] == settings.discovery.shortlist_path

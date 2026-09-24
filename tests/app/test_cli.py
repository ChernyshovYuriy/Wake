from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hlsignals.app.cli import Runtime, main
from hlsignals.core.clock import Clock, FakeClock, FakeSleeper, from_ms
from hlsignals.core.errors import RetryableError
from hlsignals.infra.tape_feed import WsConnection
from hlsignals.infra.transport import FixtureTransport, Payload, Transport
from tests.conftest import FIXTURE_DIR, load_fixture
from tests.factories import AS_OF

FILLS = load_fixture("fills_active")["request"]
FIXTURE_NOW = from_ms(FILLS["endTime"])


@pytest.fixture
def config(tmp_path: Path) -> Path:
    """A config that keeps the census database out of the repository's data/ dir."""
    path = tmp_path / "config.toml"
    path.write_text(f'[census]\ndb_path = "{tmp_path / "census.sqlite"}"\n')
    return path


def runtime(transport: Transport | None = None, clock: Clock | None = None, **kw: Any) -> Runtime:
    clock = clock or FakeClock(FIXTURE_NOW)
    return Runtime(
        env={},
        clock=clock,
        sleeper=FakeSleeper(),
        transport=lambda settings, c: transport or FixtureTransport(FIXTURE_DIR),
        connect=kw.get("connect", _no_ws),
    )


def _no_ws(url: str) -> Any:
    raise AssertionError("no websocket expected")


def cli(
    argv: list[str], capsys: pytest.CaptureFixture[str], rt: Runtime | None = None
) -> tuple[int, str, str]:
    code = main(argv, runtime=rt or runtime())
    out, err = capsys.readouterr()
    return code, out, err


# --- vet ---------------------------------------------------------------------------------


def test_vet_real_fixture_wallet(config: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = cli(
        ["--config", str(config), "vet", "--wallet", FILLS["user"], "--lookback-days", "3"], capsys
    )
    assert code == 0
    assert FILLS["user"] in out
    assert "REJECTED min_sample" in out
    assert "hit_rate" in out


def test_vet_wallets_file_reports_bad_entries(
    config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wallets = tmp_path / "w.toml"
    wallets.write_text(
        f'[[wallets]]\naddress = "{FILLS["user"]}"\n\n[[wallets]]\naddress = "bad"\n'
    )
    code, out, err = cli(
        ["--config", str(config), "vet", "--wallets-file", str(wallets), "--lookback-days", "3"],
        capsys,
    )
    assert code == 0
    assert "skipped entry" in err
    assert FILLS["user"] in out


@pytest.mark.parametrize("args", [["vet"], ["vet", "--wallet", "0x123"]])
def test_vet_argument_errors_are_config_errors(
    config: Path, args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = cli(["--config", str(config), *args], capsys)
    assert code == 2
    assert "config error" in err


# --- run and exit codes ----------------------------------------------------------------------


def test_run_with_only_failing_wallet_sources_exits_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "c.toml"
    config.write_text(
        f'[wallets]\nprecedence = ["curated"]\n\n[[wallets.sources]]\ntype = "curated"\n'
        f'path = "{tmp_path / "missing.toml"}"\n'
    )
    code, _, err = cli(["--config", str(config), "run"], capsys)
    assert code == 3
    assert "all wallet sources failed" in err


class Down(Transport):
    def post(self, payload: Payload) -> Any:
        raise RetryableError("HTTP 503", status=503)


def test_run_api_failure_exits_4(config: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = cli(["--config", str(config), "run"], capsys, runtime(transport=Down()))
    assert code == 4
    assert "API error" in err


def test_bad_config_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "c.toml"
    config.write_text("[signals]\nmin_walets = 3\n")
    code, _, err = cli(["--config", str(config), "run"], capsys)
    assert code == 2
    assert "min_walets" in err


def test_run_on_fixtures_with_no_wallets_reports_all_insufficient(
    config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_file = tmp_path / "report.json"
    code, out, _ = cli(
        ["--config", str(config), "run", "--format", "json", "--output", str(out_file)], capsys
    )
    assert code == 0
    assert out == ""
    report = json.loads(out_file.read_text())
    assert report["signals"]
    assert {s["status"] for s in report["signals"]} == {"insufficient"}
    assert report["diagnostics"]["universe_discovered"] == 80


def test_run_fixtures_dir_without_scenario_is_config_error(
    config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = cli(["--config", str(config), "run", "--fixtures", str(tmp_path)], capsys)
    assert code == 2
    assert "scenario.toml" in err


# --- census -------------------------------------------------------------------------------------


class TickingClock:
    """Advances one minute per reading, so a --minutes deadline is reached."""

    def __init__(self) -> None:
        self.t = AS_OF

    def now(self) -> datetime:
        self.t += timedelta(minutes=1)
        return self.t


class Ws:
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)

    def recv(self, timeout: float) -> str:
        if not self.messages:
            raise TimeoutError
        return self.messages.pop(0)


def test_census_records_tracked_trades(
    config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trades = load_fixture("recent_trades_xyz_nvda")["response"]
    ws = Ws([json.dumps({"channel": "trades", "data": trades})])

    @contextmanager
    def connect(url: str) -> Iterator[WsConnection]:
        yield ws

    code, out, _ = cli(
        ["--config", str(config), "census", "--minutes", "3"],
        capsys,
        runtime(clock=TickingClock(), connect=connect),
    )
    assert code == 0
    assert f"census: {len(trades)} trades" in out
    assert (tmp_path / "census.sqlite").exists()
    assert len(ws.sent) > 50  # one subscription per US-stock market


# --- capture-fixtures -> run --fixtures ---------------------------------------------------------


def test_capture_then_replay_reproduces_the_live_signals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wallets = tmp_path / "wallets.toml"
    wallets.write_text(f'[[wallets]]\naddress = "{FILLS["user"]}"\nscore = 60.0\n')
    config = tmp_path / "config.toml"
    config.write_text(
        f'[census]\ndb_path = "{tmp_path / "census.sqlite"}"\n\n'
        f'[wallets]\nprecedence = ["curated"]\n\n'
        f'[[wallets.sources]]\ntype = "curated"\npath = "{wallets}"\n'
    )
    scenario = tmp_path / "scenario"
    code, out, _ = cli(
        [
            "--config",
            str(config),
            "capture-fixtures",
            "--out",
            str(scenario),
            "--lookback-days",
            "3",
        ],
        capsys,
    )
    assert code == 0
    assert "1 wallets" in out
    assert (scenario / "scenario.toml").exists()
    assert FILLS["user"] not in (scenario / "wallets.toml").read_text()  # pseudonymized

    code, out, _ = cli(
        ["--config", str(config), "run", "--fixtures", str(scenario), "--format", "json"], capsys
    )
    assert code == 0
    replayed = json.loads(out)
    live = json.loads((scenario / "live_report.json").read_text())
    assert replayed["as_of"] == live["as_of"]
    assert [s["symbol"] for s in replayed["signals"]] == [s["symbol"] for s in live["signals"]]
    assert [s["components"] for s in replayed["signals"]] == [
        s["components"] for s in live["signals"]
    ]
    for key in (
        "universe_discovered",
        "universe_after_filters",
        "wallets_considered",
        "wallets_rejected",
    ):
        assert replayed["diagnostics"][key] == live["diagnostics"][key]


def test_verbose_logs_progress(config: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = cli(["-v", "--config", str(config), "run"], capsys)
    assert code == 0
    assert "universe:" in err

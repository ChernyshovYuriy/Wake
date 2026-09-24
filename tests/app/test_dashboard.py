from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from hlsignals.app.config import Settings
from hlsignals.app.dashboard import create_app
from hlsignals.app.discover import DiscoveryStore
from hlsignals.app.vet import VetResult
from hlsignals.backtest.report import backtest_to_json
from hlsignals.core.clock import FakeClock, to_ms
from hlsignals.reporting.renderers import JsonRenderer
from hlsignals.wallets.census.registry import SqliteRegistry
from tests.backtest.test_report import report as backtest_report
from tests.factories import AS_OF, make_scored_wallet, make_wallet_record, wallet_address
from tests.reporting.sample import sample_report

NOW_MS = to_ms(AS_OF)
GOOD, BAD = wallet_address(1), wallet_address(2)

SHOW = """Id=hlsignals-census.service
LoadState=loaded
ActiveState=active
SubState=running
Result=success
ActiveEnterTimestamp=Thu 2026-09-24 08:00:00 EDT

Id=hlsignals-discover.service
LoadState=loaded
ActiveState=failed
SubState=failed
Result=exit-code
"""


def client(tmp: Path, runner_output: str | None = SHOW) -> FlaskClient:
    def runner(argv: list[str]) -> str:
        if runner_output is None:
            raise FileNotFoundError("systemctl")
        return runner_output

    app = create_app(Settings(), base_dir=tmp, clock=FakeClock(AS_OF), runner=runner)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def populated(tmp_path: Path) -> Path:
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    signals = JsonRenderer().render(sample_report())
    (reports / "signals-2026-09-24.json").write_text(signals)
    (reports / "signals-latest.json").write_text(signals)
    bt = backtest_to_json(backtest_report(40, walk_forward=True))
    (reports / "backtest-2026-09-20.json").write_text(bt)
    (reports / "backtest-latest.json").write_text(bt)
    registry = SqliteRegistry(tmp_path / "data" / "census.sqlite")
    for i in range(25):
        registry.observe([GOOD, BAD], NOW_MS - 60_000 - i)
    registry.close()
    store = DiscoveryStore(tmp_path / "data" / "discovery.sqlite")
    store.record(
        VetResult(
            make_wallet_record(address=GOOD),
            make_scored_wallet(address=GOOD, trust=0.61),
            None,
            None,
        ),
        NOW_MS,
    )
    store.record(
        VetResult(
            make_wallet_record(address=BAD), None, ("maker_profile", "prescreen: 2000 fills"), None
        ),
        NOW_MS,
    )
    store.close()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wallets.toml").write_text(
        f'[[wallets]]\naddress = "{GOOD}"\nnote = "from Hyperdash"\n'
    )
    return tmp_path


def text(c: FlaskClient, url: str) -> str:
    response = c.get(url)
    assert response.status_code == 200, url
    return response.get_data(as_text=True)


def test_healthz(tmp_path: Path) -> None:
    assert client(tmp_path).get("/healthz").get_json() == {"ok": True}


def test_overview_shows_services_funnel_signals_and_verdict(populated: Path) -> None:
    page = text(client(populated), "/")
    assert "hlsignals-census.service" in page
    assert "active (running)" in page
    assert "failed (failed), result exit-code" in page  # a failed job is visible
    assert "seen by the census" in page
    assert "NVDA" in page  # top signal
    assert "recording" in page  # census wrote 1 minute ago
    assert "BEAT buy-and-hold" in page
    assert 'http-equiv="refresh"' in page


def test_signals_page_latest_and_by_day(populated: Path) -> None:
    c = client(populated)
    page = text(c, "/signals")
    for needle in (
        "NVDA",
        "TSLA",
        "thin_volume",
        "net_usd",
        "Accepted wallets",
        "maker_profile",
        "2026-09-24",
    ):
        assert needle in page
    assert "NVDA" in text(c, "/signals?day=2026-09-24")
    assert "No report for 2026-01-01" in text(c, "/signals?day=2026-01-01")
    assert c.get("/signals?day=yesterday").status_code == 400


def test_wallets_page(populated: Path) -> None:
    page = text(client(populated), "/wallets")
    assert GOOD in page
    assert "0.610" in page
    assert "from Hyperdash" in page
    assert "maker_profile" in page
    assert "https://app.hyperliquid.xyz/explorer/address/" in page


def test_census_page(populated: Path) -> None:
    page = text(client(populated), "/census")
    assert "recording" in page
    assert GOOD in page
    assert "New wallets per day" in page


def test_backtest_page(populated: Path) -> None:
    c = client(populated)
    page = text(c, "/backtest")
    for needle in (
        "BEAT buy-and-hold",
        "buy-and-hold",
        "Walk-forward folds",
        "open interest is approximated",
    ):
        assert needle in page
    assert "BEAT" in text(c, "/backtest?day=2026-09-20")


@pytest.mark.parametrize("url", ["/", "/signals", "/wallets", "/census", "/backtest"])
def test_every_page_works_on_a_fresh_install_without_systemd(tmp_path: Path, url: str) -> None:
    page = text(client(tmp_path, runner_output=None), url)
    assert "hl-whale-signals" in page
    assert not (tmp_path / "data").exists()  # viewing never creates files


def test_fresh_install_messages(tmp_path: Path) -> None:
    c = client(tmp_path, runner_output=None)
    assert "systemd units are not available" in text(c, "/")
    assert "No saved report yet" in text(c, "/")
    assert "Discovery has not run yet" in text(c, "/wallets")
    assert "No census database yet" in text(c, "/census")
    assert "No backtest yet" in text(c, "/backtest")


def test_a_corrupt_report_is_shown_as_an_error_not_a_crash(populated: Path) -> None:
    (populated / "data" / "reports" / "signals-latest.json").write_text(
        json.dumps({"schema_version": 99})
    )
    page = text(client(populated), "/signals")
    assert "could not be read" in page

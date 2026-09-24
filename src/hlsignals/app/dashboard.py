"""Read-only LAN web dashboard: the state of the system at a glance.

Like StockScanner's dashboard (Flask under Waitress, LAN-only, no authentication), but
read-only: it shows what the scheduled jobs produced and whether they are running. It
never triggers a job or changes a file.

    hlsignals dashboard            # http://<pi>:8081
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request

from hlsignals.app.config import Settings
from hlsignals.app.dashboard_data import (
    DashboardPaths,
    age_days,
    backtest_days,
    census_stats,
    curated_wallets,
    discovery_state,
    load_backtest,
    load_signal_report,
    signal_report_days,
)
from hlsignals.app.system_status import UNITS, Runner, unit_statuses
from hlsignals.core.clock import MS_PER_HOUR, Clock, from_ms, to_ms
from hlsignals.core.errors import AdapterError
from hlsignals.domain.models import SignalReport, SignalStatus
from hlsignals.reporting.evidence import DISCLAIMER, fmt_evidence, fmt_flags, fmt_score, fmt_value

_RECENT_DECISIONS = 50
EXPLORER = "https://app.hyperliquid.xyz/explorer/address/"


def _day_arg() -> date | None:
    raw = request.args.get("day")
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        abort(400, f"not a date: {raw!r}")


def create_app(settings: Settings, *, base_dir: Path, clock: Clock, runner: Runner) -> Flask:
    app = Flask(__name__)
    paths = DashboardPaths.from_settings(settings, base_dir)
    app.jinja_env.filters.update(
        score=fmt_score,
        value=fmt_value,
        evidence=fmt_evidence,
        flags=fmt_flags,
        when=lambda ms: "-" if ms is None else from_ms(ms).strftime("%Y-%m-%d %H:%M UTC"),
        pct=lambda v: "-" if v is None else f"{v:+.2%}",
        num=lambda v: "-" if v is None else f"{v:.2f}",
    )
    app.jinja_env.globals.update(disclaimer=DISCLAIMER, explorer=EXPLORER)

    def now_ms() -> int:
        return to_ms(clock.now())

    def census() -> Any:
        return census_stats(
            paths,
            now_ms(),
            min_observations=settings.discovery.min_observations,
            live_minutes=settings.dashboard.census_live_minutes,
        )

    def signal_report(day: date | None) -> tuple[SignalReport | None, str | None]:
        try:
            return load_signal_report(paths, day), None
        except (AdapterError, ValueError) as exc:
            return None, f"the saved report could not be read: {exc}"

    @app.get("/healthz")
    def healthz() -> Any:
        return jsonify({"ok": True})

    @app.get("/")
    def overview() -> str:
        report, error = signal_report(None)
        report_age_h = None
        if report is not None:
            report_age_h = (now_ms() - to_ms(report.as_of)) / MS_PER_HOUR
        scored = [s for s in report.signals if s.status is SignalStatus.SCORED] if report else []
        discovery = discovery_state(paths, limit=0)
        return render_template(
            "overview.html",
            units=unit_statuses(UNITS, runner),
            report=report,
            report_error=error,
            report_age_h=report_age_h,
            scored=scored,
            census=census(),
            discovery=discovery,
            discovery_age_d=age_days(discovery.last_run_ms if discovery else None, now_ms()),
            backtest=load_backtest(paths, None),
        )

    @app.get("/signals")
    def signals() -> str:
        day = _day_arg()
        report, error = signal_report(day)
        scored = [s for s in report.signals if s.status is SignalStatus.SCORED] if report else []
        insufficient = (
            [s for s in report.signals if s.status is not SignalStatus.SCORED] if report else []
        )
        return render_template(
            "signals.html",
            report=report,
            error=error,
            day=day,
            days=signal_report_days(paths),
            scored=scored,
            insufficient=insufficient,
        )

    @app.get("/wallets")
    def wallets() -> str:
        curated, curated_error = curated_wallets(paths)
        return render_template(
            "wallets.html",
            discovery=discovery_state(paths, limit=_RECENT_DECISIONS),
            curated=curated,
            curated_error=curated_error,
            curated_path=paths.curated,
        )

    @app.get("/census")
    def census_page() -> str:
        stats = census()
        peak = max((n for _, n in stats.new_per_day), default=0) if stats else 0
        return render_template("census.html", stats=stats, peak=peak, settings=settings)

    @app.get("/backtest")
    def backtest() -> str:
        day = _day_arg()
        return render_template(
            "backtest.html", doc=load_backtest(paths, day), day=day, days=backtest_days(paths)
        )

    return app

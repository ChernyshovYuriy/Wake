"""Command line: run | vet | census | capture-fixtures.

Exit codes (IMPLEMENTATION_PLAN.md §6.13): 0 ok, 2 config error, 3 all wallet sources
failed, 4 API error.

``run --fixtures DIR`` replays a scenario recorded by ``capture-fixtures``: the recorded
responses, the recorded as-of time and the frozen wallet list, with no network access.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from pathlib import Path

from hlsignals.app.builder import PipelineBuilder, open_registry
from hlsignals.app.config import Settings, load_settings
from hlsignals.app.pipeline import SignalPipeline
from hlsignals.app.vet import render_vet
from hlsignals.app.wiring import build_transport, load_catalog, load_equities
from hlsignals.core.clock import (
    Clock,
    FakeClock,
    Sleeper,
    SystemClock,
    SystemSleeper,
    from_ms,
    to_ms,
)
from hlsignals.core.errors import AdapterError, ConfigError, SourceError, TransportError
from hlsignals.domain.address import normalize_address
from hlsignals.domain.models import TapeTrade, WalletRecord
from hlsignals.infra.recording import RecordingTransport, pseudonym
from hlsignals.infra.tape_feed import TapeFeed, WsConnection, websocket_connect
from hlsignals.infra.transport import FixtureTransport, Transport
from hlsignals.reporting.renderers import REPORT_FORMATS, renderer_for
from hlsignals.wallets.census.recorder import CensusRecorder
from hlsignals.wallets.census.tape import TapeSubject
from hlsignals.wallets.sources.base import SourceResult
from hlsignals.wallets.sources.curated import CuratedSource

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_SOURCES = 3
EXIT_API = 4

TransportFactory = Callable[[Settings, Clock], Transport]
Connect = Callable[[str], AbstractContextManager[WsConnection]]


@dataclasses.dataclass(frozen=True, slots=True)
class Runtime:
    """Everything that touches the outside world, injectable for tests."""

    env: Mapping[str, str]
    clock: Clock
    sleeper: Sleeper
    transport: TransportFactory
    connect: Connect


class _StderrHandler(logging.Handler):
    """Writes to whatever sys.stderr is at emit time (not the stream at setup)."""

    def emit(self, record: logging.LogRecord) -> None:
        sys.stderr.write(self.format(record) + "\n")


def live_transport(settings: Settings, clock: Clock) -> Transport:
    return build_transport(settings.api, clock, SystemSleeper())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hlsignals")
    parser.add_argument("--config", type=Path, help="TOML config (default: built-in defaults)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log progress to stderr")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="build the ranked signal report")
    run.add_argument("--format", choices=sorted(REPORT_FORMATS), help="report format")
    run.add_argument("--output", type=Path, help="write the report here instead of stdout")
    run.add_argument("--fixtures", type=Path, help="replay a recorded scenario directory")

    vet = commands.add_parser("vet", help="score wallets and explain filter decisions")
    vet.add_argument("--wallet", action="append", default=[], help="wallet address")
    vet.add_argument("--wallets-file", type=Path, help="curated wallets TOML")
    vet.add_argument("--lookback-days", type=float, help="history window to fetch")

    census = commands.add_parser("census", help="record wallets trading US stocks (websocket)")
    census.add_argument("--minutes", type=float, help="stop after this long (default: Ctrl-C)")

    capture = commands.add_parser("capture-fixtures", help="record a live run for replay")
    capture.add_argument("--out", type=Path, required=True, help="scenario directory")
    capture.add_argument("--lookback-days", type=float, help="history window to fetch")
    capture.add_argument("--max-wallets", type=int, help="keep at most this many wallets")
    return parser


def main(argv: Sequence[str] | None = None, *, runtime: Runtime | None = None) -> int:
    args = _parser().parse_args(argv)
    handler = _StderrHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, handlers=[handler], force=True
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is noise
    rt = runtime or Runtime(
        env=os.environ,
        clock=SystemClock(),
        sleeper=SystemSleeper(),
        transport=live_transport,
        connect=websocket_connect,
    )
    try:
        settings = load_settings(args.config)
        if getattr(args, "lookback_days", None) is not None:
            history = dataclasses.replace(settings.history, lookback_days=args.lookback_days)
            settings = dataclasses.replace(settings, history=history)
        command: Callable[[argparse.Namespace, Settings, Runtime], int] = {
            "run": _run,
            "vet": _vet,
            "census": _census,
            "capture-fixtures": _capture,
        }[args.command]
        return command(args, settings, rt)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except SourceError as exc:
        print(f"wallet source error: {exc}", file=sys.stderr)
        return EXIT_SOURCES
    except (TransportError, AdapterError) as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return EXIT_API


def _pipeline(
    settings: Settings, clock: Clock, transport: Transport, env: Mapping[str, str]
) -> SignalPipeline:
    return (
        PipelineBuilder()
        .with_settings(settings)
        .with_clock(clock)
        .with_transport(transport)
        .with_env(env)
        .build()
    )


def _run(args: argparse.Namespace, settings: Settings, rt: Runtime) -> int:
    if args.fixtures is not None:
        as_of, settings, transport = _scenario(args.fixtures, settings)
        clock: Clock = FakeClock(as_of)
    else:
        clock, transport = rt.clock, rt.transport(settings, rt.clock)
        as_of = clock.now()
    report = _pipeline(settings, clock, transport, rt.env).run(as_of)
    text = renderer_for(args.format or settings.report.format).render(report)
    if args.output is None:
        print(text, end="")
    else:
        args.output.write_text(text)
    return EXIT_OK


def _scenario(directory: Path, base: Settings) -> tuple[datetime, Settings, Transport]:
    try:
        meta = tomllib.loads((directory / "scenario.toml").read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"not a recorded scenario (no scenario.toml): {directory}") from exc
    as_of = datetime.fromisoformat(meta["as_of"])
    history = dataclasses.replace(base.history, lookback_days=float(meta["lookback_days"]))
    sources = dataclasses.replace(
        base.wallet_sources,
        sources=({"type": "curated", "path": str(directory / "wallets.toml")},),
        precedence=("curated",),
    )
    settings = dataclasses.replace(base, history=history, wallet_sources=sources)
    return as_of, settings, FixtureTransport(directory / "hl")


def _vet(args: argparse.Namespace, settings: Settings, rt: Runtime) -> int:
    if not args.wallet and args.wallets_file is None:
        raise ConfigError("vet needs --wallet and/or --wallets-file")
    now = rt.clock.now()
    records = [WalletRecord(_address(a), "cli", None, None, now) for a in args.wallet]
    if args.wallets_file is not None:
        result = CuratedSource(args.wallets_file, rt.clock).fetch()
        for diagnostic in result.diagnostics:
            print(f"{diagnostic.source}: {diagnostic.message}", file=sys.stderr)
        records.extend(result.records)
    pipeline = _pipeline(settings, rt.clock, rt.transport(settings, rt.clock), rt.env)
    print(render_vet(pipeline.vet(records, now)), end="")
    return EXIT_OK


def _address(raw: str) -> str:
    try:
        return normalize_address(raw)
    except AdapterError as exc:
        raise ConfigError(str(exc)) from exc


def _census(args: argparse.Namespace, settings: Settings, rt: Runtime) -> int:
    base = Path()
    equities = load_equities(
        load_catalog(base / settings.universe.instruments_path), settings.universe.include_classes
    )
    registry = open_registry(settings, base)
    subject = TapeSubject()
    subject.subscribe(CensusRecorder(registry, equities, settings.census.dedupe_capacity))
    seen: list[int] = [0]

    def on_trades(trades: list[TapeTrade]) -> None:
        seen[0] += len(trades)
        for trade in trades:
            subject.publish(trade)

    deadline = None if args.minutes is None else rt.clock.now() + timedelta(minutes=args.minutes)
    feed = TapeFeed(
        url=settings.api.ws_url,
        symbols=sorted(equities),
        on_trades=on_trades,
        connect=rt.connect,
        sleeper=rt.sleeper,
        recv_timeout_s=settings.census.recv_timeout_s,
        reconnect_delay_s=settings.census.reconnect_delay_s,
    )
    try:
        feed.run(lambda: deadline is not None and rt.clock.now() >= deadline)
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    wallets = registry.observations(min_fills=1)
    registry.close()
    print(
        f"census: {seen[0]} trades on {len(equities)} US-stock markets; "
        f"{len(wallets)} wallets in {settings.census.db_path}"
    )
    return EXIT_OK


def _capture(args: argparse.Namespace, settings: Settings, rt: Runtime) -> int:
    as_of = from_ms(to_ms(rt.clock.now()))  # millisecond precision, as in every request
    recorder = RecordingTransport(rt.transport(settings, rt.clock))
    pipeline = _pipeline(settings, rt.clock, recorder, rt.env)
    fetched = pipeline.fetch_wallets()
    records = fetched.records[: args.max_wallets] if args.max_wallets else fetched.records
    frozen = SourceResult(records, fetched.diagnostics, fetched.used, fetched.failed)
    report = pipeline.run(as_of, sources=frozen)
    out: Path = args.out
    count = recorder.save(out / "hl")
    (out / "wallets.toml").write_text(_wallets_toml(records))
    (out / "scenario.toml").write_text(
        f'as_of = "{as_of.isoformat()}"\nlookback_days = {settings.history.lookback_days}\n'
    )
    (out / "live_report.json").write_text(renderer_for("json").render(report))
    print(f"captured {count} requests, {len(records)} wallets, as of {as_of.isoformat()} -> {out}")
    return EXIT_OK


def _wallets_toml(records: Sequence[WalletRecord]) -> str:
    lines = ["# Frozen, pseudonymized wallet list of a recorded scenario.", ""]
    for record in records:
        lines += ["[[wallets]]", f'address = "{pseudonym(record.address)}"']
        if record.raw_score is not None:
            lines.append(f"score = {record.raw_score!r}")
        if record.raw_metric is not None:
            lines.append(f'metric = "{record.raw_metric}"')
        lines += [f"as_of = {record.as_of.isoformat()}", f'note = "source: {record.source}"', ""]
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

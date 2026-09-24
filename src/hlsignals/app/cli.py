"""Command line entry point. Exit codes: 0 ok, 2 config error, 3 all sources failed,
4 API error (IMPLEMENTATION_PLAN.md §6.13)."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from hlsignals.app.config import Settings
from hlsignals.app.vet import VetResult, WalletVetter, render_vet
from hlsignals.app.wiring import (
    build_gateway,
    build_wallet_filter_chain,
    build_wallet_scorer,
    load_catalog,
    load_equities,
)
from hlsignals.core.clock import Clock, SystemClock, SystemSleeper
from hlsignals.core.errors import AdapterError, ConfigError, SourceError, TransportError
from hlsignals.domain.address import normalize_address
from hlsignals.domain.models import WalletRecord
from hlsignals.wallets.sources.curated import CuratedSource

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_SOURCES = 3
EXIT_API = 4


class Vetter(Protocol):
    def vet_all(self, records: list[WalletRecord]) -> list[VetResult]: ...


VetterFactory = Callable[[Settings, Clock], Vetter]


def live_vetter(settings: Settings, clock: Clock) -> Vetter:
    catalog = load_catalog(Path(settings.universe.instruments_path))
    return WalletVetter(
        gateway=build_gateway(settings.api, clock, SystemSleeper()),
        clock=clock,
        equities=load_equities(catalog, settings.universe.include_classes),
        chain=build_wallet_filter_chain(settings.wallet_filters),
        scorer=build_wallet_scorer(settings.scoring),
        settings=settings.vet,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hlsignals")
    commands = parser.add_subparsers(dest="command", required=True)
    vet = commands.add_parser("vet", help="score wallets and explain filter decisions")
    group = vet.add_argument_group("wallets (at least one)")
    group.add_argument("--wallet", action="append", default=[], help="wallet address")
    group.add_argument("--wallets-file", type=Path, help="curated wallets TOML")
    vet.add_argument("--lookback-days", type=float, help="history window to fetch")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    make_vetter: VetterFactory = live_vetter,
    clock: Clock | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.wallet and args.wallets_file is None:
        parser.error("vet needs --wallet and/or --wallets-file")
    clock = clock or SystemClock()
    settings = Settings()
    if args.lookback_days is not None:
        settings = dataclasses.replace(
            settings, vet=dataclasses.replace(settings.vet, lookback_days=args.lookback_days)
        )
    try:
        records = _records(args.wallet, args.wallets_file, clock)
        results = make_vetter(settings, clock).vet_all(records)
    except (ConfigError, AdapterError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except SourceError as exc:
        print(f"wallet source error: {exc}", file=sys.stderr)
        return EXIT_SOURCES
    except TransportError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return EXIT_API
    print(render_vet(results), end="")
    return EXIT_OK


def _records(addresses: list[str], wallets_file: Path | None, clock: Clock) -> list[WalletRecord]:
    now = clock.now()
    records = [WalletRecord(normalize_address(a), "cli", None, None, now) for a in addresses]
    if wallets_file is not None:
        result = CuratedSource(wallets_file, clock).fetch()
        for diagnostic in result.diagnostics:
            print(f"{diagnostic.source}: {diagnostic.message}", file=sys.stderr)
        records.extend(result.records)
    return records


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

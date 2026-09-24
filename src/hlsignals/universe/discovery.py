"""Discover the signal universe: which dexes list included instruments, and their markets.

Only dexes that list at least one catalog symbol of an included class are queried, in
``preference`` order then by name. One failing dex is recorded and skipped. When the same
coin trades on several dexes, the preferred dex's market is kept and the others are
recorded as shadowed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from hlsignals.core.errors import AdapterError, TransportError
from hlsignals.domain.models import Dex, MarketCtx
from hlsignals.domain.symbols import Symbol
from hlsignals.universe.instruments import InstrumentCatalog


class MarketDataPort(Protocol):
    """What discovery needs from the exchange (HyperliquidGateway satisfies it)."""

    def perp_dexs(self) -> list[Dex]: ...

    def meta_and_ctxs(self, dex: str) -> list[MarketCtx]: ...


@dataclass(frozen=True, slots=True)
class Discovery:
    markets: tuple[MarketCtx, ...]
    dexes_queried: tuple[str, ...]
    dexes_skipped: tuple[str, ...]  # live dexes listing nothing we include
    dex_failures: Mapping[str, str]
    unclassified: tuple[Symbol, ...]  # live markets missing from the catalog
    excluded_by_class: Mapping[str, int]
    shadowed: Mapping[Symbol, Symbol]  # dropped duplicate -> kept market


class EquityDexLocator:
    def __init__(
        self,
        port: MarketDataPort,
        catalog: InstrumentCatalog,
        include_classes: frozenset[str],
        preference: Sequence[str],
    ) -> None:
        catalog.check_classes(include_classes)
        self._port = port
        self._catalog = catalog
        self._include = include_classes
        self._preference = tuple(preference)

    def locate(self) -> Discovery:
        live = {d.name for d in self._port.perp_dexs()}
        wanted = {s.dex for s in self._catalog.symbols_in(self._include)}
        candidates = live & wanted
        ordered = [d for d in self._preference if d in candidates]
        ordered += sorted(candidates - set(ordered))

        kept: dict[str, MarketCtx] = {}  # coin -> market
        failures: dict[str, str] = {}
        unclassified: list[Symbol] = []
        excluded: Counter[str] = Counter()
        shadowed: dict[Symbol, Symbol] = {}
        for dex in ordered:
            try:
                markets = self._port.meta_and_ctxs(dex)
            except (TransportError, AdapterError) as exc:
                failures[dex] = f"{type(exc).__name__}: {exc}"
                continue
            for market in markets:
                asset_class = self._catalog.class_of(market.symbol)
                if asset_class is None:
                    if not market.is_delisted:
                        unclassified.append(market.symbol)
                elif asset_class not in self._include:
                    excluded[asset_class] += 1
                elif market.symbol.coin in kept:
                    shadowed[market.symbol] = kept[market.symbol.coin].symbol
                else:
                    kept[market.symbol.coin] = market
        return Discovery(
            markets=tuple(kept.values()),
            dexes_queried=tuple(ordered),
            dexes_skipped=tuple(sorted(live - candidates)),
            dex_failures=failures,
            unclassified=tuple(unclassified),
            excluded_by_class=dict(excluded),
            shadowed=shadowed,
        )

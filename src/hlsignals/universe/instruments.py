"""Instrument catalog: the asset class of every HIP-3 market (docs/api-notes.md §1).

The API has no asset-class metadata, so ``config/instruments.toml`` is the only source.
A symbol absent from the catalog is unclassified and never signalled.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from hlsignals.core.errors import AdapterError, ConfigError
from hlsignals.domain.symbols import Symbol


@dataclass(frozen=True, slots=True)
class InstrumentCatalog:
    classes: Mapping[Symbol, str]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> InstrumentCatalog:
        if "classes" not in raw:
            raise ConfigError("instrument catalog is missing [classes]")
        by_symbol: dict[Symbol, str] = {}
        for class_name, symbols in raw["classes"].items():
            if not isinstance(symbols, list):
                raise ConfigError(f"instrument class {class_name!r} must be a list of symbols")
            for raw_symbol in symbols:
                symbol = _parse(raw_symbol)
                if symbol in by_symbol:
                    raise ConfigError(f"{symbol} is listed in more than one class")
                by_symbol[symbol] = class_name
        return cls(MappingProxyType(by_symbol))

    @property
    def class_names(self) -> frozenset[str]:
        return frozenset(self.classes.values())

    def class_of(self, symbol: Symbol) -> str | None:
        return self.classes.get(symbol)

    def symbols_in(self, include: frozenset[str]) -> frozenset[Symbol]:
        return frozenset(s for s, c in self.classes.items() if c in include)

    def check_classes(self, include: frozenset[str]) -> None:
        unknown = include - self.class_names
        if unknown:
            raise ConfigError(
                f"unknown instrument class(es) {sorted(unknown)}; "
                f"catalog has {sorted(self.class_names)}"
            )


def _parse(raw: str) -> Symbol:
    try:
        symbol = Symbol.parse(raw)
    except AdapterError as exc:
        raise ConfigError(f"invalid catalog symbol {raw!r}: {exc}") from exc
    if not symbol.is_hip3:
        raise ConfigError(f"catalog symbol {raw!r} is not a HIP-3 (dex:coin) market")
    return symbol

"""WalletSource Template Method: ``fetch() = load raw -> adapt -> validate -> dedupe``.

Subclasses supply ``_load_raw`` and ``_adapt``. Validation (canonical ``0x`` + 40 hex
address, domain checks) and dedupe (keep the newest ``as_of`` per address) are shared.
An entry that fails adapting or validation becomes a diagnostic, not a crash. A source
that cannot load at all raises SourceError.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, final

from hlsignals.core.errors import AdapterError
from hlsignals.domain.address import normalize_address
from hlsignals.domain.models import WalletRecord


@dataclass(frozen=True, slots=True)
class RawWallet:
    """A wallet as a source reports it, before validation."""

    address: str
    as_of: datetime
    raw_score: float | None = None
    raw_metric: str | None = None


@dataclass(frozen=True, slots=True)
class SourceDiagnostic:
    source: str
    message: str


@dataclass(frozen=True, slots=True)
class SourceResult:
    records: tuple[WalletRecord, ...]
    diagnostics: tuple[SourceDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class Loaded:
    items: Sequence[Any]
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


class WalletSourcePort(Protocol):
    @property
    def name(self) -> str: ...

    def fetch(self) -> SourceResult: ...


class WalletSource(ABC):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @final
    def fetch(self) -> SourceResult:
        loaded = self._load_raw()
        diagnostics = [SourceDiagnostic(self.name, m) for m in loaded.diagnostics]
        records: list[WalletRecord] = []
        for item in loaded.items:
            try:
                records.append(self._validate(self._adapt(item)))
            except (AdapterError, ValueError) as exc:
                diagnostics.append(SourceDiagnostic(self.name, f"skipped entry: {exc}"))
        return SourceResult(self._dedupe(records), tuple(diagnostics))

    @abstractmethod
    def _load_raw(self) -> Loaded:
        """Load raw entries; raise SourceError if the source is unusable."""

    @abstractmethod
    def _adapt(self, item: Any) -> RawWallet:
        """Convert one raw entry; raise AdapterError/ValueError to skip it."""

    def _validate(self, raw: RawWallet) -> WalletRecord:
        return WalletRecord(
            address=normalize_address(raw.address),
            source=self.name,
            raw_score=raw.raw_score,
            raw_metric=raw.raw_metric,
            as_of=raw.as_of,
        )

    @staticmethod
    def _dedupe(records: Sequence[WalletRecord]) -> tuple[WalletRecord, ...]:
        newest: dict[str, WalletRecord] = {}
        for record in records:
            kept = newest.get(record.address)
            if kept is None or record.as_of > kept.as_of:
                newest[record.address] = record
        return tuple(newest.values())

"""CompositeWalletSource: blends sources, isolating failures.

On an address listed by several sources, the record from the source earliest in
``precedence`` wins (default: the order given). A failing source becomes a diagnostic.
Only when every source fails is SourceError raised.
"""

from __future__ import annotations

from collections.abc import Sequence

from hlsignals.core.errors import ConfigError, SourceError
from hlsignals.domain.models import WalletRecord
from hlsignals.wallets.sources.base import SourceDiagnostic, SourceResult, WalletSourcePort


class CompositeWalletSource:
    def __init__(
        self,
        sources: Sequence[WalletSourcePort],
        precedence: Sequence[str] | None = None,
        name: str = "composite",
    ) -> None:
        if not sources:
            raise ConfigError("a composite wallet source needs at least one source")
        names = [s.name for s in sources]
        if len(names) != len(set(names)):
            raise ConfigError(f"duplicate wallet source names: {names}")
        order = list(precedence or names)
        unknown = set(order) - set(names)
        if unknown:
            raise ConfigError(f"precedence names unknown source(s): {sorted(unknown)}")
        order += [n for n in names if n not in order]
        by_name = {s.name: s for s in sources}
        self._sources = [by_name[n] for n in order]
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def fetch(self) -> SourceResult:
        merged: dict[str, WalletRecord] = {}
        diagnostics: list[SourceDiagnostic] = []
        failures: list[str] = []
        for source in self._sources:
            try:
                result = source.fetch()
            except SourceError as exc:
                failures.append(source.name)
                diagnostics.append(SourceDiagnostic(source.name, f"failed: {exc}"))
                continue
            diagnostics.extend(result.diagnostics)
            for record in result.records:
                merged.setdefault(record.address, record)
        if len(failures) == len(self._sources):
            details = "; ".join(d.message for d in diagnostics)
            raise SourceError(f"all wallet sources failed: {details}")
        used = tuple(s.name for s in self._sources if s.name not in failures)
        return SourceResult(
            tuple(merged.values()), tuple(diagnostics), used=used, failed=tuple(failures)
        )

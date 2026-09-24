"""Report renderers (Strategy): terminal table, Markdown, and versioned JSON.

The JSON form is a stable, versioned schema; ``report_from_json`` reads it back into a
SignalReport losslessly, which is how the schema is kept honest in tests.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from hlsignals.core.errors import AdapterError
from hlsignals.domain.models import (
    Diagnostics,
    FeatureValue,
    MarketCtx,
    ScoredWallet,
    SignalComponent,
    SignalDirection,
    SignalFlag,
    SignalReport,
    SignalStatus,
    TickerSignal,
)
from hlsignals.domain.symbols import Symbol
from hlsignals.reporting.evidence import (
    DISCLAIMER,
    component_names,
    diagnostics_lines,
    fmt_evidence,
    fmt_flags,
    fmt_score,
    market_line,
    wallet_feature_lines,
    wallet_line,
)

SCHEMA_VERSION = 1
_TIME_FORMAT = "%Y-%m-%d %H:%M UTC"


class Renderer(Protocol):
    def render(self, report: SignalReport) -> str: ...


def _split(report: SignalReport) -> tuple[list[TickerSignal], list[TickerSignal]]:
    scored = [s for s in report.signals if s.status is SignalStatus.SCORED]
    return scored, [s for s in report.signals if s.status is SignalStatus.INSUFFICIENT]


def _summary_rows(signals: Sequence[TickerSignal], names: list[str]) -> list[list[str]]:
    header = ["#", "ticker", "dir", "score", *names, "wallets", "flags"]
    rows = [header]
    for rank, s in enumerate(signals, start=1):
        values = [fmt_score(s.components[n].value) if n in s.components else "-" for n in names]
        direction = s.direction.value if s.direction else "-"
        rows.append(
            [
                str(rank),
                s.symbol.coin,
                direction,
                fmt_score(s.score),
                *values,
                str(s.n_wallets),
                fmt_flags(s),
            ]
        )
    return rows


class TableRenderer:
    """Fixed-width plain text for the terminal."""

    def render(self, report: SignalReport) -> str:
        scored, insufficient = _split(report)
        names = component_names(report.signals)
        out = [
            f"hl-whale-signals report, as of {report.as_of.strftime(_TIME_FORMAT)}",
            DISCLAIMER,
            "",
        ]
        if scored:
            out += _aligned(_summary_rows(scored, names))
        else:
            out.append("No ticker has enough corroboration to be scored.")
        if insufficient:
            out += ["", "Insufficient corroboration (not scored):"]
            out += [
                f"  {s.symbol.coin:<8} wallets {s.n_wallets}   {s.reason}" for s in insufficient
            ]
        if report.signals:
            out += ["", "Evidence:"]
            for s in report.signals:
                out.append(f"{s.symbol} [{s.status.value}] {s.reason}")
                out.append(f"  market     {market_line(s)}   flags: {fmt_flags(s)}")
                for name, c in s.components.items():
                    out.append(f"  {name:<10} {fmt_score(c.value)}   {fmt_evidence(c.evidence)}")
        out += ["", f"Accepted wallets ({len(report.wallets)}):"]
        for w in report.wallets:
            out.append(f"  {wallet_line(w)}")
            out += [f"    {line}" for line in wallet_feature_lines(w)]
        out += ["", "Diagnostics:"]
        out += [f"  {label}: {text}" for label, text in diagnostics_lines(report.diagnostics)]
        return "\n".join(out) + "\n"


def _aligned(rows: list[list[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return [
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]


class MarkdownRenderer:
    def render(self, report: SignalReport) -> str:
        scored, insufficient = _split(report)
        names = component_names(report.signals)
        out = [
            f"# hl-whale-signals report, as of {report.as_of.strftime(_TIME_FORMAT)}",
            "",
            f"_{DISCLAIMER}_",
            "",
            "## Ranked signals",
            "",
        ]
        if scored:
            header, *rows = _summary_rows(scored, names)
            out += [_md_row(header), _md_row(["---"] * len(header)), *map(_md_row, rows)]
        else:
            out.append("No ticker has enough corroboration to be scored.")
        if insufficient:
            out += ["", "## Insufficient corroboration (not scored)", ""]
            out += [
                f"- **{s.symbol.coin}** (wallets {s.n_wallets}): {s.reason}" for s in insufficient
            ]
        if report.signals:
            out += ["", "## Evidence"]
            for s in report.signals:
                out += [
                    "",
                    f"### {s.symbol} ({s.status.value})",
                    "",
                    f"{s.reason}",
                    "",
                    f"- market: {market_line(s)}",
                    f"- flags: {fmt_flags(s)}",
                ]
                out += [
                    f"- **{name}** {fmt_score(c.value)}: {fmt_evidence(c.evidence)}"
                    for name, c in s.components.items()
                ]
        out += ["", f"## Accepted wallets ({len(report.wallets)})", ""]
        for w in report.wallets:
            out.append(f"- `{w.address}`: {wallet_line(w).split('  ', 1)[1]}")
            out += [f"  - {line}" for line in wallet_feature_lines(w)]
        out += ["", "## Diagnostics", ""]
        out += [f"- {label}: {text}" for label, text in diagnostics_lines(report.diagnostics)]
        return "\n".join(out) + "\n"


def _md_row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


class JsonRenderer:
    def render(self, report: SignalReport) -> str:
        doc = {
            "schema_version": SCHEMA_VERSION,
            "as_of": report.as_of.isoformat(),
            "disclaimer": DISCLAIMER,
            "signals": [_signal_to_json(s) for s in report.signals],
            "wallets": [_wallet_to_json(w) for w in report.wallets],
            "diagnostics": _diagnostics_to_json(report.diagnostics),
        }
        return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _signal_to_json(s: TickerSignal) -> dict[str, Any]:
    m = s.market
    return {
        "symbol": str(s.symbol),
        "status": s.status.value,
        "direction": s.direction.value if s.direction else None,
        "score": s.score,
        "n_wallets": s.n_wallets,
        "reason": s.reason,
        "flags": sorted(f.value for f in s.flags),
        "components": {
            name: {"value": c.value, "evidence": dict(c.evidence)}
            for name, c in s.components.items()
        },
        "market": {
            "mark_px": m.mark_px,
            "oracle_px": m.oracle_px,
            "prev_day_px": m.prev_day_px,
            "mid_px": m.mid_px,
            "open_interest": m.open_interest,
            "oi_usd": m.oi_usd,
            "day_ntl_vlm": m.day_ntl_vlm,
            "funding": m.funding,
            "is_delisted": m.is_delisted,
        },
    }


def _wallet_to_json(w: ScoredWallet) -> dict[str, Any]:
    return {
        "address": w.address,
        "trust": w.trust,
        "confidence": w.confidence,
        "decay": w.decay,
        "n_round_trips": w.n_closed_lots,
        "track_record_days": w.track_record_days,
        "features": {
            name: {"value": f.value, "evidence": dict(f.evidence)} for name, f in w.features.items()
        },
    }


def _wallet_from_json(raw: Mapping[str, Any]) -> ScoredWallet:
    return ScoredWallet(
        address=raw["address"],
        trust=raw["trust"],
        confidence=raw["confidence"],
        decay=raw["decay"],
        features={
            name: FeatureValue(f["value"], f["evidence"]) for name, f in raw["features"].items()
        },
        n_closed_lots=raw["n_round_trips"],
        track_record_days=raw["track_record_days"],
    )


def _diagnostics_to_json(d: Diagnostics) -> dict[str, Any]:
    return {
        "sources_used": list(d.sources_used),
        "sources_failed": list(d.sources_failed),
        "source_messages": list(d.source_messages),
        "wallets_considered": d.wallets_considered,
        "wallets_accepted": d.wallets_accepted,
        "wallets_rejected": dict(d.wallets_rejected),
        "wallets_truncated": d.wallets_truncated,
        "universe_discovered": d.universe_discovered,
        "universe_after_filters": d.universe_after_filters,
        "markets_rejected": dict(d.markets_rejected),
        "unclassified_symbols": list(d.unclassified_symbols),
        "dex_failures": dict(d.dex_failures),
        "notes": list(d.notes),
    }


def report_from_json(text: str) -> SignalReport:
    """Parse a JSON report of the current schema version back into a SignalReport."""
    doc = json.loads(text)
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise AdapterError(f"unsupported report schema_version {doc.get('schema_version')!r}")
    try:
        return SignalReport(
            as_of=datetime.fromisoformat(doc["as_of"]),
            signals=tuple(_signal_from_json(s) for s in doc["signals"]),
            diagnostics=_diagnostics_from_json(doc["diagnostics"]),
            wallets=tuple(_wallet_from_json(w) for w in doc["wallets"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AdapterError(f"malformed report JSON: {exc}") from exc


def _signal_from_json(raw: Mapping[str, Any]) -> TickerSignal:
    symbol = Symbol.parse(raw["symbol"])
    market = {k: v for k, v in raw["market"].items() if k != "oi_usd"}  # derived field
    return TickerSignal(
        symbol=symbol,
        status=SignalStatus(raw["status"]),
        direction=None if raw["direction"] is None else SignalDirection(raw["direction"]),
        score=raw["score"],
        components={
            name: SignalComponent(c["value"], c["evidence"])
            for name, c in raw["components"].items()
        },
        n_wallets=raw["n_wallets"],
        reason=raw["reason"],
        flags=frozenset(SignalFlag(f) for f in raw["flags"]),
        market=MarketCtx(symbol=symbol, **market),
    )


def _diagnostics_from_json(raw: Mapping[str, Any]) -> Diagnostics:
    return Diagnostics(
        sources_used=tuple(raw["sources_used"]),
        sources_failed=tuple(raw["sources_failed"]),
        source_messages=tuple(raw["source_messages"]),
        wallets_considered=raw["wallets_considered"],
        wallets_accepted=raw["wallets_accepted"],
        wallets_rejected=raw["wallets_rejected"],
        wallets_truncated=raw["wallets_truncated"],
        universe_discovered=raw["universe_discovered"],
        universe_after_filters=raw["universe_after_filters"],
        markets_rejected=raw["markets_rejected"],
        unclassified_symbols=tuple(raw["unclassified_symbols"]),
        dex_failures=raw["dex_failures"],
        notes=tuple(raw["notes"]),
    )


_RENDERERS: Mapping[str, type[TableRenderer] | type[MarkdownRenderer] | type[JsonRenderer]] = {
    "table": TableRenderer,
    "markdown": MarkdownRenderer,
    "json": JsonRenderer,
}


REPORT_FORMATS: frozenset[str] = frozenset(_RENDERERS)


def renderer_for(fmt: str) -> Renderer:
    if fmt not in _RENDERERS:
        raise ValueError(f"unknown report format {fmt!r}; valid: {', '.join(sorted(_RENDERERS))}")
    return _RENDERERS[fmt]()

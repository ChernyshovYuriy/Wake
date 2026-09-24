"""Formatting shared by the text renderers (single home for how numbers look)."""

from __future__ import annotations

from collections.abc import Mapping

from hlsignals.domain.models import Diagnostics, EvidenceValue, ScoredWallet, TickerSignal

DISCLAIMER = "Research signal derived from Hyperliquid HIP-3 perp activity. Not financial advice."
_THOUSAND = 1_000


def fmt_value(value: EvidenceValue) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if abs(value) >= _THOUSAND:
            return f"{value:,.0f}"
        return f"{value:.2f}" if abs(value) >= 1 else f"{value:.4g}"
    return str(value)


def fmt_score(value: float | None) -> str:
    return "-" if value is None else f"{value:+.3f}"


def fmt_usd(value: float) -> str:
    return f"${value:,.0f}"


def fmt_evidence(evidence: Mapping[str, EvidenceValue]) -> str:
    return ", ".join(f"{key}={fmt_value(value)}" for key, value in evidence.items())


def fmt_flags(signal: TickerSignal) -> str:
    return ", ".join(sorted(flag.value for flag in signal.flags)) or "-"


def fmt_counts(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{name} {n}" for name, n in sorted(counts.items())) or "none"


def market_line(signal: TickerSignal) -> str:
    m = signal.market
    return (
        f"mark {fmt_value(m.mark_px)}, open interest {fmt_usd(m.oi_usd)}, "
        f"24h volume {fmt_usd(m.day_ntl_vlm)}"
    )


def component_names(signals: tuple[TickerSignal, ...]) -> list[str]:
    names: list[str] = []
    for signal in signals:
        names.extend(n for n in signal.components if n not in names)
    return names


def diagnostics_lines(d: Diagnostics) -> list[tuple[str, str]]:
    """(label, text) pairs, in reading order."""
    return [
        ("wallet sources used", ", ".join(d.sources_used) or "none"),
        ("wallet sources failed", ", ".join(d.sources_failed) or "none"),
        *(("source note", message) for message in d.source_messages),
        (
            "wallets",
            f"{d.wallets_accepted} accepted of {d.wallets_considered} considered; "
            f"rejected by filter: {fmt_counts(d.wallets_rejected)}",
        ),
        ("wallets with truncated history", str(d.wallets_truncated)),
        (
            "universe",
            f"{d.universe_after_filters} markets after filters of {d.universe_discovered} "
            f"discovered; rejected by filter: {fmt_counts(d.markets_rejected)}",
        ),
        ("unclassified symbols", ", ".join(d.unclassified_symbols) or "none"),
        (
            "dex failures",
            "; ".join(f"{dex}: {why}" for dex, why in sorted(d.dex_failures.items())) or "none",
        ),
        *(("note", note) for note in d.notes),
    ]


def wallet_line(w: ScoredWallet) -> str:
    return (
        f"{w.address}  trust {w.trust:.3f} = base x confidence {w.confidence:.3f} "
        f"x decay {w.decay:.3f}; {w.n_closed_lots} round trips over "
        f"{w.track_record_days:.1f} days"
    )


def wallet_feature_lines(w: ScoredWallet) -> list[str]:
    return [f"{name} {f.value:.3f}: {fmt_evidence(f.evidence)}" for name, f in w.features.items()]

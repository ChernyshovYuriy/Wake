"""Plain-language explanations of every term on the Signals page, shown as hover tips.

The single home of that wording. Thresholds are read from the settings that the signal
engine is built from, so a tip always states the rule the report was actually run with.
"""

from __future__ import annotations

from dataclasses import dataclass

from hlsignals.app.config import SignalSettings
from hlsignals.domain.models import SignalDirection, SignalFlag, SignalStatus

_TRUST_WEIGHTED = (
    "Trust-weighted: each wallet's dollars are multiplied by its trust (0..1), so these "
    "are not raw dollar amounts."
)


@dataclass(frozen=True, slots=True)
class Glossary:
    columns: dict[str, str]  # table headers and the market line
    directions: dict[str, str]
    statuses: dict[str, str]
    flags: dict[str, str]
    evidence: dict[str, dict[str, str]]  # component -> evidence key -> tip
    wallet: dict[str, str]  # accepted-wallets table headers


def build_glossary(s: SignalSettings) -> Glossary:
    weights = " + ".join(f"{w:g} x {name}" for name, w in s.weights.items())
    total = sum(s.weights.values())
    window = f"{s.flow_window_hours:g}h"
    return Glossary(
        columns={
            "stock": "The US stock, and the Hyperliquid perpetual contract it trades as "
            "(dex:ticker). All prices are that perp's, not the exchange quote.",
            "direction": "The call: long = trusted wallets lean bullish (expect the price to "
            "rise), short = they lean bearish (expect it to fall), flat = no clear lean.",
            "score": f"Combined signal in [-1, +1] = ({weights}) / {total:g}. "
            f"Above +{s.epsilon:g} is long, below -{s.short_epsilon:g} short, otherwise flat. "
            "Shorts may need stronger evidence: in backtests they have been the weaker calls.",
            "tilt": "How trusted wallets are positioned right now: +1 = all their open "
            "positions are long, -1 = all short, 0 = balanced or none.",
            "flow": f"Trusted wallets' net buying over the last {window}, relative to open "
            f"interest. Counts as 0 below {s.min_flow_oi_frac:.0%} of OI; reaches +/-1 at "
            f"{s.flow_full_scale_oi_frac:.0%}.",
            "overnight": "The perp's price move since the last US cash-market close. Counts "
            f"as 0 below {s.min_overnight:.1%}; reaches +/-1 at {s.overnight_full_scale:.0%}.",
            "wallets": f"Distinct trusted wallets (trust >= {s.min_trust:g}) that hold this "
            f"perp now or traded it in the last {s.corroboration_window_hours:g}h. At least "
            f"{s.min_wallets} are needed for a score.",
            "flags": "Warnings that weaken the signal; hover each one.",
            "mark": "Mark price: the perp's current fair price in USD, used by Hyperliquid "
            "for margin and profit/loss. It tracks the stock but is not the stock quote.",
            "open_interest": "Open interest: the USD value of all positions currently open in "
            "this perp (contracts x mark). A gauge of how much money is committed.",
            "volume": "24h volume: USD value traded in this perp over the last 24 hours.",
            "reason": f"Why: the score is compared with +{s.epsilon:g} (epsilon, for long) and "
            f"-{s.short_epsilon:g} (short_epsilon, for short), then the count of trusted "
            f"wallets with {s.min_wallets} required. "
            "'Involved' counts every wallet in this stock, trusted or not.",
        },
        directions={
            SignalDirection.LONG: f"Long: score above +{s.epsilon:g}. Trusted-wallet "
            "evidence is bullish: the price is expected to rise.",
            SignalDirection.SHORT: f"Short: score below -{s.short_epsilon:g}. Trusted-wallet "
            "evidence is bearish: the price is expected to fall.",
            SignalDirection.FLAT: f"Flat: score between -{s.short_epsilon:g} and "
            f"+{s.epsilon:g}. Evidence is too "
            "weak or mixed to call a direction.",
        },
        statuses={
            SignalStatus.SCORED: f"Scored: at least {s.min_wallets} trusted wallets are "
            "involved, so a direction was computed.",
            SignalStatus.INSUFFICIENT: f"Insufficient: fewer than {s.min_wallets} trusted "
            "wallets involved. Components are shown for reference, but there is no call.",
        },
        flags={
            SignalFlag.THIN_VOLUME: f"24h volume below ${s.thin_volume_usd:,.0f}: the perp "
            "trades little, so its prices and flows are easier to move.",
            SignalFlag.WEAK_SAMPLE: f"The wallets behind this signal have average confidence "
            f"below {s.weak_confidence:g}: their track records are short.",
            SignalFlag.STALE_OVERNIGHT_REF: "The latest price candle is older than "
            f"{s.max_overnight_staleness_hours:g}h, so the overnight move may be out of date.",
            SignalFlag.CASH_SESSION_OPEN: "The US cash market was open when this ran, so the "
            "'overnight' move is partly an intraday move.",
        },
        evidence={
            "tilt": {
                "net_usd": "Long value minus short value of trusted wallets' positions. "
                + _TRUST_WEIGHTED,
                "gross_usd": "Long value plus short value of trusted wallets' positions. "
                + _TRUST_WEIGHTED,
                "tilt": "net_usd / gross_usd, the component value before weighting.",
                "n_long": "Wallets holding a long position.",
                "n_short": "Wallets holding a short position.",
                "n_wallets": "Wallets with a position in this perp.",
                "mark_px": "Mark price used to value the positions.",
            },
            "flow": {
                "flow_usd": f"Buys minus sells in the last {window}, in USD. " + _TRUST_WEIGHTED,
                "oi_usd": "Open interest in USD, the yardstick flow is divided by.",
                "flow_oi_frac": f"flow_usd / oi_usd. Below {s.min_flow_oi_frac:.0%} the "
                f"component is 0; {s.flow_full_scale_oi_frac:.0%} maps to +/-1.",
                "n_fills": f"Trades (fills) by tracked wallets in the last {window}.",
                "n_wallets": f"Wallets that traded in the last {window}.",
                "window_hours": "Look-back window for flow, in hours.",
                "note": "Why the component is 0.",
            },
            "overnight": {
                "ref_px": "Perp price at the last US cash-market close.",
                "last_px": "Latest perp price.",
                "pct": "Simple move: last_px / ref_px - 1 (0.01 = 1%).",
                "log_return": f"ln(last_px / ref_px), the value that is scaled: below "
                f"{s.min_overnight:.1%} it is 0, {s.overnight_full_scale:.0%} maps to +/-1.",
                "stale": f"true when the latest candle is older than "
                f"{s.max_overnight_staleness_hours:g}h.",
                "note": "Why the component is 0.",
            },
        },
        wallet={
            "wallet": "Hyperliquid address; opens the explorer.",
            "trust": "How much this wallet's activity counts (0..1) = track-record quality x "
            "confidence x decay.",
            "confidence": "How much to believe the track record: grows with the number of "
            "completed round trips.",
            "decay": "Recency: 1 when it traded stocks recently, shrinking the longer it has "
            "been idle.",
            "round_trips": "Positions opened and fully closed, the basis of its track record.",
        },
    )

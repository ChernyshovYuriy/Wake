"""Signal features (Strategy): each maps TickerInputs to a SignalComponent in [-1, 1].

Sign convention: + bullish, - bearish. Each component carries the raw inputs behind it.
"""

from __future__ import annotations

import math
from typing import Protocol

from hlsignals.core.clock import MS_PER_HOUR
from hlsignals.core.mathx import clamp, safe_div
from hlsignals.domain.candles import price_at
from hlsignals.domain.direction import signed_notional
from hlsignals.domain.models import SignalComponent
from hlsignals.signals.inputs import TickerInputs


class SignalFeature(Protocol):
    @property
    def name(self) -> str: ...

    def compute(self, inputs: TickerInputs) -> SignalComponent: ...


def _require(ok: bool, what: str) -> None:
    if not ok:
        raise ValueError(f"invalid signal feature parameter: {what}")


class PositioningFeature:
    """Tilt = trust-weighted net notional / trust-weighted gross notional, at mark price."""

    name = "tilt"

    def compute(self, inputs: TickerInputs) -> SignalComponent:
        mark = inputs.market.mark_px
        net = gross = 0.0
        n_long = n_short = 0
        for position in inputs.positions:
            weighted = inputs.trust(position.wallet) * float(position.size) * mark
            net += weighted
            gross += abs(weighted)
            n_long += position.size > 0
            n_short += position.size < 0
        tilt = clamp(safe_div(net, gross, default=0.0), -1.0, 1.0)
        evidence = {
            "net_usd": net,
            "gross_usd": gross,
            "tilt": tilt,
            "n_long": n_long,
            "n_short": n_short,
            "n_wallets": len({p.wallet for p in inputs.positions}),
            "mark_px": mark,
        }
        return SignalComponent(tilt, evidence)


class FlowFeature:
    """Trust-weighted signed notional traded in (as_of - window, as_of], divided by OI in
    USD, scaled so ``full_scale_oi_frac`` maps to +/-1; below ``min_oi_frac`` it is 0."""

    name = "flow"

    def __init__(self, window_hours: float, min_oi_frac: float, full_scale_oi_frac: float) -> None:
        _require(window_hours > 0, f"window_hours={window_hours}")
        _require(0 <= min_oi_frac < full_scale_oi_frac, "need 0 <= min_oi_frac < full_scale")
        self.window_ms = int(window_hours * MS_PER_HOUR)
        self.window_hours = window_hours
        self.min_oi_frac = min_oi_frac
        self.full_scale = full_scale_oi_frac

    def compute(self, inputs: TickerInputs) -> SignalComponent:
        start = inputs.as_of_ms - self.window_ms
        in_window = [f for f in inputs.fills if start < f.time_ms <= inputs.as_of_ms]
        flow = sum(inputs.trust(f.wallet) * signed_notional(f) for f in in_window)
        oi = inputs.market.oi_usd
        evidence: dict[str, float | int | str | bool | None] = {
            "flow_usd": flow,
            "oi_usd": oi,
            "n_fills": len(in_window),
            "n_wallets": len({f.wallet for f in in_window}),
            "window_hours": self.window_hours,
        }
        if oi <= 0:
            evidence["note"] = "no open interest: flow cannot be normalized"
            return SignalComponent(0.0, evidence)
        frac = flow / oi
        evidence["flow_oi_frac"] = frac
        if abs(frac) < self.min_oi_frac:
            evidence["note"] = f"|flow/OI| below the {self.min_oi_frac:.1%} floor"
            return SignalComponent(0.0, evidence)
        return SignalComponent(clamp(frac / self.full_scale, -1.0, 1.0), evidence)


class OvernightFeature:
    """Perp move since the last US cash close, as a log return, scaled so
    ``full_scale_move`` maps to +/-1; below ``min_move`` it is 0.

    Reference: the close of the last 1h candle closed by the cash close. Latest: the
    close of the last candle closed by as_of; stale when that candle is older than
    ``max_staleness_hours`` (off-hours perp prices can be thin, docs/api-notes.md §7).
    """

    name = "overnight"

    def __init__(self, min_move: float, full_scale_move: float, max_staleness_hours: float) -> None:
        _require(0 <= min_move < full_scale_move, "need 0 <= min_move < full_scale_move")
        _require(max_staleness_hours > 0, f"max_staleness_hours={max_staleness_hours}")
        self.min_move = min_move
        self.full_scale = full_scale_move
        self.max_staleness_ms = int(max_staleness_hours * MS_PER_HOUR)

    def compute(self, inputs: TickerInputs) -> SignalComponent:
        candles = inputs.candles
        if not candles:
            return SignalComponent(0.0, {"note": "no candles", "stale": True})
        ref = price_at(candles, inputs.last_close_ms)
        last = price_at(candles, inputs.as_of_ms)
        if ref is None or last is None:
            return SignalComponent(
                0.0, {"note": "no candle closed by the last close", "stale": True}
            )
        latest_close_ms = max(c.close_ms for c in candles if c.close_ms <= inputs.as_of_ms)
        log_move = math.log(last / ref)
        evidence: dict[str, float | int | str | bool | None] = {
            "ref_px": ref,
            "last_px": last,
            "pct": last / ref - 1,
            "log_return": log_move,
            "stale": inputs.as_of_ms - latest_close_ms > self.max_staleness_ms,
        }
        if abs(log_move) < self.min_move:
            evidence["note"] = f"|move| below the {self.min_move:.2%} floor"
            return SignalComponent(0.0, evidence)
        return SignalComponent(clamp(log_move / self.full_scale, -1.0, 1.0), evidence)

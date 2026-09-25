"""SignalEngine: features -> corroboration -> combine -> flags, for one ticker.

Components are computed (and reported) even for INSUFFICIENT tickers, so the report can
show what the signal would have been.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from hlsignals.domain.models import (
    SignalComponent,
    SignalDirection,
    SignalFlag,
    SignalStatus,
    TickerSignal,
)
from hlsignals.signals.combiner import WeightedCombiner
from hlsignals.signals.corroboration import Corroboration, CorroborationResult
from hlsignals.signals.features import SignalFeature
from hlsignals.signals.inputs import TickerInputs


@dataclass(frozen=True, slots=True)
class FlagThresholds:
    thin_volume_usd: float  # 24h volume below this -> THIN_VOLUME
    weak_confidence: float  # mean confidence of corroborating wallets below -> WEAK_SAMPLE


class SignalEngine:
    def __init__(
        self,
        features: Sequence[SignalFeature],
        corroboration: Corroboration,
        combiner: WeightedCombiner,
        flags: FlagThresholds,
    ) -> None:
        names = [f.name for f in features]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate signal features: {names}")
        unweighted = set(combiner.weights) - set(names)
        if unweighted:
            raise ValueError(f"weights for features that are not computed: {sorted(unweighted)}")
        self.features = tuple(features)
        self.corroboration = corroboration
        self.combiner = combiner
        self.flags = flags

    def evaluate(self, inputs: TickerInputs) -> TickerSignal:
        components = {f.name: f.compute(inputs) for f in self.features}
        corroboration = self.corroboration.check(inputs)
        if corroboration.sufficient:
            score, direction = self.combiner.combine(components)
            # Worded from the combiner's decision: the epsilon boundary lives only there.
            comparison = "<=" if direction is SignalDirection.FLAT else ">"
            status = SignalStatus.SCORED
            reason = (
                f"{direction.value}: |score {score:+.3f}| {comparison} epsilon "
                f"{self.combiner.epsilon}; {corroboration.reason}"
            )
        else:
            score, direction = None, None
            status = SignalStatus.INSUFFICIENT
            reason = corroboration.reason
        return TickerSignal(
            symbol=inputs.symbol,
            status=status,
            direction=direction,
            score=score,
            components=components,
            n_wallets=corroboration.n_wallets,
            reason=reason,
            flags=self._flags(inputs, components, corroboration),
            market=inputs.market,
        )

    def _flags(
        self,
        inputs: TickerInputs,
        components: Mapping[str, SignalComponent],
        corroboration: CorroborationResult,
    ) -> frozenset[SignalFlag]:
        flags = set()
        if inputs.market.day_ntl_vlm < self.flags.thin_volume_usd:
            flags.add(SignalFlag.THIN_VOLUME)
        confidences = [inputs.wallets[w].confidence for w in corroboration.wallets]
        if not confidences or statistics.fmean(confidences) < self.flags.weak_confidence:
            flags.add(SignalFlag.WEAK_SAMPLE)
        if any(c.evidence.get("stale") is True for c in components.values()):
            flags.add(SignalFlag.STALE_OVERNIGHT_REF)
        if inputs.session_open:
            flags.add(SignalFlag.CASH_SESSION_OPEN)
        return frozenset(flags)

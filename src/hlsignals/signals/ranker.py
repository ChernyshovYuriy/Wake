"""Ranking: scored names by |score| descending (ties by ticker), then insufficient ones."""

from __future__ import annotations

from collections.abc import Iterable

from hlsignals.domain.models import SignalStatus, TickerSignal


def rank(signals: Iterable[TickerSignal]) -> list[TickerSignal]:
    def key(signal: TickerSignal) -> tuple[int, float, str]:
        scored = signal.status is SignalStatus.SCORED
        return (0 if scored else 1, -abs(signal.score or 0.0), str(signal.symbol))

    return sorted(signals, key=key)

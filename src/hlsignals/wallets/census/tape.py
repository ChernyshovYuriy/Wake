"""Tape subject (Observer): fans public trades out to independent observers.

One failing observer is logged and counted but never stops the others.
"""

from __future__ import annotations

import logging
from typing import Protocol

from hlsignals.domain.models import TapeTrade

logger = logging.getLogger(__name__)


class TradeObserver(Protocol):
    def on_trade(self, trade: TapeTrade) -> None: ...


class TapeSubject:
    def __init__(self) -> None:
        self._observers: list[TradeObserver] = []
        self.observer_errors = 0

    def subscribe(self, observer: TradeObserver) -> None:
        self._observers.append(observer)

    def unsubscribe(self, observer: TradeObserver) -> None:
        if observer not in self._observers:
            raise ValueError(f"observer {observer!r} is not subscribed")
        self._observers.remove(observer)

    def publish(self, trade: TapeTrade) -> None:
        for observer in list(self._observers):
            try:
                observer.on_trade(trade)
            except Exception:
                self.observer_errors += 1
                logger.exception("tape observer %r failed on trade tid=%s", observer, trade.tid)

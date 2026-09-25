from __future__ import annotations

import logging

import pytest

from hlsignals.domain.models import TapeTrade
from hlsignals.wallets.census.recorder import CensusRecorder
from hlsignals.wallets.census.registry import InMemoryRegistry, WalletObservation
from hlsignals.wallets.census.tape import TapeSubject
from tests.factories import BTC, NVDA, OTHER_WALLET, WALLET, make_tape_trade


class Collector:
    def __init__(self, log: list[tuple[str, int]], name: str) -> None:
        self.log, self.name = log, name

    def on_trade(self, trade: TapeTrade) -> None:
        self.log.append((self.name, trade.tid))


class Exploding:
    def on_trade(self, trade: TapeTrade) -> None:
        raise RuntimeError("observer bug")


def test_subscribe_publish_in_order_to_all_observers() -> None:
    log: list[tuple[str, int]] = []
    subject = TapeSubject()
    subject.subscribe(Collector(log, "a"))
    subject.subscribe(Collector(log, "b"))
    t1, t2 = make_tape_trade(), make_tape_trade()
    subject.publish(t1)
    subject.publish(t2)
    assert log == [("a", t1.tid), ("b", t1.tid), ("a", t2.tid), ("b", t2.tid)]


def test_unsubscribe() -> None:
    log: list[tuple[str, int]] = []
    subject = TapeSubject()
    observer = Collector(log, "a")
    subject.subscribe(observer)
    subject.unsubscribe(observer)
    subject.publish(make_tape_trade())
    assert log == []
    with pytest.raises(ValueError, match="not subscribed"):
        subject.unsubscribe(observer)


def test_observer_exception_is_isolated_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    log: list[tuple[str, int]] = []
    subject = TapeSubject()
    subject.subscribe(Exploding())
    subject.subscribe(Collector(log, "after"))
    with caplog.at_level(logging.ERROR):
        subject.publish(make_tape_trade())
    assert len(log) == 1
    assert "observer bug" in caplog.text
    assert subject.observer_errors == 1


def recorder(capacity: int = 100) -> tuple[CensusRecorder, InMemoryRegistry]:
    registry = InMemoryRegistry()
    return CensusRecorder(registry, frozenset({NVDA}), dedupe_capacity=capacity), registry


def test_recorder_upserts_both_counterparties() -> None:
    rec, registry = recorder()
    rec.on_trade(make_tape_trade(time_ms=7))
    rec.on_trade(make_tape_trade(time_ms=9))
    for address in (WALLET, OTHER_WALLET):
        assert registry.get(address) == WalletObservation(
            address, first_seen_ms=7, last_seen_ms=9, n_fills=2
        )


def test_recorder_ignores_untracked_symbols() -> None:
    rec, registry = recorder()
    rec.on_trade(make_tape_trade(symbol=BTC))
    assert registry.observations(min_fills=0) == []


def test_recorder_ignores_duplicate_tid_across_reconnects() -> None:
    rec, registry = recorder()
    trade = make_tape_trade()
    rec.on_trade(trade)
    rec.on_trade(trade)  # replayed snapshot after reconnect
    observation = registry.get(WALLET)
    assert observation is not None
    assert observation.n_fills == 1


def test_recorder_dedupe_memory_is_bounded() -> None:
    rec, registry = recorder(capacity=1)
    first, second = make_tape_trade(), make_tape_trade()
    rec.on_trade(first)
    rec.on_trade(second)  # evicts first from the dedupe window
    rec.on_trade(first)
    observation = registry.get(WALLET)
    assert observation is not None
    assert observation.n_fills == 3


def test_recorder_capacity_validated() -> None:
    with pytest.raises(ValueError, match="capacity"):
        CensusRecorder(InMemoryRegistry(), frozenset(), dedupe_capacity=0)

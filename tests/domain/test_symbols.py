from __future__ import annotations

import pytest

from hlsignals.core.errors import AdapterError
from hlsignals.domain.symbols import Symbol


def test_parse_hip3() -> None:
    s = Symbol.parse("xyz:GOOGL")
    assert (s.dex, s.coin) == ("xyz", "GOOGL")
    assert s.is_hip3


@pytest.mark.parametrize("raw", ["BTC", "kPEPE", "@142", "PURR/USDC", "#25510"])
def test_parse_non_hip3_keeps_case(raw: str) -> None:
    s = Symbol.parse(raw)
    assert (s.dex, s.coin) == (None, raw)
    assert not s.is_hip3


def test_dex_lower_cased_coin_case_preserved() -> None:
    assert Symbol.parse("XYZ:NVDA") == Symbol("xyz", "NVDA")
    assert Symbol.parse("xyz:kFoo").coin == "kFoo"


@pytest.mark.parametrize(
    "raw", ["xyz:", ":GOOGL", "a:b:c", "", " ", " xyz:NVDA", "xyz:NV DA", "x y:NVDA"]
)
def test_parse_rejects_malformed(raw: str) -> None:
    with pytest.raises(AdapterError):
        Symbol.parse(raw)


@pytest.mark.parametrize("raw", ["xyz:GOOGL", "BTC", "para:10Y", "@142"])
def test_round_trip(raw: str) -> None:
    assert str(Symbol.parse(raw)) == raw


def test_constructor_validates() -> None:
    with pytest.raises(AdapterError):
        Symbol("XYZ", "NVDA")
    with pytest.raises(AdapterError):
        Symbol("xyz", "")


def test_hashable_and_ordered() -> None:
    assert len({Symbol.parse("xyz:A"), Symbol.parse("xyz:A")}) == 1
    assert sorted([Symbol.parse("xyz:B"), Symbol.parse("xyz:A")])[0].coin == "A"

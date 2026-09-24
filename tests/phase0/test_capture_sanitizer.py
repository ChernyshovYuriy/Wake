from __future__ import annotations

import re
from types import ModuleType

import pytest

from hlsignals.infra import recording

REAL = "0x82fd11271061ad9b2e6b856e2beb3ad1e4d7f316"
TX_HASH = "0x" + "ab" * 32


@pytest.fixture(scope="module")
def capture() -> ModuleType:
    return recording


def test_pseudonym_is_valid_deterministic_and_case_insensitive(capture: ModuleType) -> None:
    alias = capture.pseudonym(REAL)
    assert re.fullmatch(r"0x[0-9a-f]{40}", alias)
    assert alias != REAL
    assert alias == capture.pseudonym(REAL.upper().replace("0X", "0x"))


def test_sanitize_replaces_addresses_in_nested_values(capture: ModuleType) -> None:
    raw = {"user": REAL, "trades": [{"users": [REAL, "x"]}], "n": 3}
    out = capture.sanitize(raw)
    alias = capture.pseudonym(REAL)
    assert out == {"user": alias, "trades": [{"users": [alias, "x"]}], "n": 3}


def test_sanitize_keeps_hashes_and_whitelisted_keys(capture: ModuleType) -> None:
    raw = {"hash": TX_HASH, "cloid": "0x" + "1" * 32, "deployer": REAL, "note": TX_HASH}
    out = capture.sanitize(raw)
    assert out["hash"] == TX_HASH
    assert out["deployer"] == REAL
    # A 64-hex string is not a 40-hex address and must survive untouched anywhere.
    assert out["note"] == TX_HASH

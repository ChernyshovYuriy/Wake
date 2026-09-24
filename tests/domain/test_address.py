from __future__ import annotations

import pytest

from hlsignals.core.errors import AdapterError
from hlsignals.domain.address import normalize_address, require_address

GOOD = "0x" + "ab" * 20


def test_normalize_lowercases() -> None:
    assert normalize_address(GOOD.upper().replace("0X", "0x")) == GOOD


@pytest.mark.parametrize(
    "bad", ["", "0x", "ab" * 20, "0x" + "ab" * 19, "0x" + "zz" * 20, " " + GOOD]
)
def test_normalize_rejects(bad: str) -> None:
    with pytest.raises(AdapterError):
        normalize_address(bad)


def test_require_address_accepts_only_canonical() -> None:
    require_address(GOOD)
    with pytest.raises(AdapterError):
        require_address(GOOD.upper().replace("0X", "0x"))

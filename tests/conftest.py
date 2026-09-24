from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hl"


def load_fixture(name: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((FIXTURE_DIR / f"{name}.json").read_text())
    return doc


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR

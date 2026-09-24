"""README must explain every report field (Phase 10 Definition of Done). Keys are
collected from real rendered reports, so a new field without documentation fails here."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hlsignals.backtest.report import backtest_to_json
from hlsignals.reporting.renderers import JsonRenderer
from tests.app.test_pipeline import report as pipeline_report
from tests.backtest.test_report import report as backtest_report
from tests.reporting.sample import sample_report

README = (Path(__file__).parents[2] / "README.md").read_text()
# Keys whose names are data, not schema (component / feature / filter names are covered
# explicitly below; map keys like wallet addresses or dex names are not fields).
DATA_KEYED = {
    "components",
    "features",
    "wallets_rejected",
    "markets_rejected",
    "dex_failures",
    "skipped",
}


def keys(node: Any, parent: str | None = None) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if parent not in DATA_KEYED:
                yield key
            yield from keys(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from keys(item, parent)


def documents() -> list[dict[str, Any]]:
    return [
        json.loads(JsonRenderer().render(pipeline_report())),
        json.loads(JsonRenderer().render(sample_report())),
        json.loads(backtest_to_json(backtest_report(40, walk_forward=True))),
    ]


FIELDS = sorted({k for doc in documents() for k in keys(doc)})
NAMES = sorted(
    {name for doc in documents()[:2] for s in doc["signals"] for name in s["components"]}
    | {name for doc in documents()[:2] for w in doc["wallets"] for name in w["features"]}
    | {"min_sample", "maker_profile", "inactivity", "reversal_bait", "api_error", "positions_error"}
    | {"delisted", "min_day_volume", "min_open_interest", "watchlist"}
    | {"thin_volume", "weak_sample", "stale_overnight_ref", "cash_session_open"}
    | {"prior_score"}
)


@pytest.mark.parametrize("field", FIELDS)
def test_every_report_field_is_documented(field: str) -> None:
    assert f"`{field}`" in README


@pytest.mark.parametrize("name", NAMES)
def test_every_component_feature_filter_and_flag_is_documented(name: str) -> None:
    assert name in README


def test_every_command_is_documented() -> None:
    for command in ("census", "run", "vet", "backtest", "capture-fixtures"):
        assert f"hlsignals {command}" in README

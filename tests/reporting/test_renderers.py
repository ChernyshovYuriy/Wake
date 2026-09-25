"""Snapshot tests: renders of the sample report must match the committed files exactly.
Regenerate after an intended change with:  UPDATE_SNAPSHOTS=1 pytest tests/reporting"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from hlsignals.core.errors import AdapterError
from hlsignals.domain.models import EvidenceValue, SignalReport
from hlsignals.reporting.evidence import fmt_value
from hlsignals.reporting.renderers import (
    SCHEMA_VERSION,
    JsonRenderer,
    MarkdownRenderer,
    Renderer,
    TableRenderer,
    renderer_for,
    report_from_json,
)
from tests.reporting.sample import empty_report, insufficient_report, sample_report

SNAPSHOTS = Path(__file__).parent / "snapshots"


@pytest.mark.parametrize(
    ("name", "renderer"),
    [
        ("table.txt", TableRenderer()),
        ("markdown.md", MarkdownRenderer()),
        ("json.json", JsonRenderer()),
    ],
)
def test_snapshot(name: str, renderer: Renderer) -> None:
    path = SNAPSHOTS / name
    rendered = renderer.render(sample_report())
    if os.environ.get("UPDATE_SNAPSHOTS"):  # pragma: no cover - regenerating, not testing
        path.write_text(rendered)
    assert rendered == path.read_text()


@pytest.mark.parametrize("build", [sample_report, empty_report, insufficient_report])
def test_json_round_trip(build: Callable[[], SignalReport]) -> None:
    report = build()
    assert report_from_json(JsonRenderer().render(report)) == report


def test_json_is_versioned_and_sorted() -> None:
    doc = json.loads(JsonRenderer().render(sample_report()))
    assert doc["schema_version"] == SCHEMA_VERSION
    assert list(doc) == sorted(doc)
    assert doc["disclaimer"]


def test_json_rejects_other_schema_versions() -> None:
    doc = json.loads(JsonRenderer().render(sample_report()))
    doc["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(AdapterError, match="schema_version"):
        report_from_json(json.dumps(doc))


def test_renderer_for_formats() -> None:
    assert isinstance(renderer_for("table"), TableRenderer)
    assert isinstance(renderer_for("markdown"), MarkdownRenderer)
    assert isinstance(renderer_for("json"), JsonRenderer)
    with pytest.raises(ValueError, match="json, markdown, table"):
        renderer_for("html")


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (-80_000.0, "-80,000"),
        (1_234.5, "1,234"),
        (224.07, "224.07"),
        (0.009, "0.009"),
        (0.0, "0"),
        (3, "3"),
        (True, "true"),
        (None, "-"),
        ("x", "x"),
    ],
)
def test_fmt_value(value: EvidenceValue, text: str) -> None:
    assert fmt_value(value) == text


def test_json_missing_field_is_adapter_error() -> None:
    doc = json.loads(JsonRenderer().render(sample_report()))
    del doc["diagnostics"]["wallets_truncated"]
    with pytest.raises(AdapterError, match="wallets_truncated"):
        report_from_json(json.dumps(doc))

"""Every Renderer honours the same contract; add a builder to cover a new one."""

from __future__ import annotations

import pytest

from hlsignals.reporting.renderers import (
    JsonRenderer,
    MarkdownRenderer,
    Renderer,
    TableRenderer,
)
from tests.reporting.sample import empty_report, insufficient_report, sample_report

RENDERERS: dict[str, Renderer] = {
    "table": TableRenderer(),
    "markdown": MarkdownRenderer(),
    "json": JsonRenderer(),
}


@pytest.fixture(params=sorted(RENDERERS))
def renderer(request: pytest.FixtureRequest) -> Renderer:
    return RENDERERS[request.param]


def test_deterministic(renderer: Renderer) -> None:
    assert renderer.render(sample_report()) == renderer.render(sample_report())


def test_empty_report(renderer: Renderer) -> None:
    text = renderer.render(empty_report())
    assert "2026-09-24" in text


def test_all_insufficient(renderer: Renderer) -> None:
    text = renderer.render(insufficient_report())
    assert "META" in text
    assert "insufficient" in text.lower()


def test_every_evidence_field_and_flag_is_present(renderer: Renderer) -> None:
    report = sample_report()
    text = renderer.render(report)
    for signal in report.signals:
        assert signal.symbol.coin in text
        assert signal.reason in text
        for flag in signal.flags:
            assert flag.value in text
        for name, component in signal.components.items():
            assert name in text
            for key in component.evidence:
                assert key in text


def test_diagnostics_rendered(renderer: Renderer) -> None:
    text = renderer.render(sample_report())
    for needle in ("maker_profile", "min_day_volume", "xyz:NEWCO", "NANSEN_API_KEY", "apify", "io"):
        assert needle in text

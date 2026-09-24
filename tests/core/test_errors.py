from __future__ import annotations

import pytest

from hlsignals.core import errors


@pytest.mark.parametrize(
    ("child", "parent"),
    [
        (errors.TransportError, errors.HLSignalsError),
        (errors.RetryableError, errors.TransportError),
        (errors.NonRetryableError, errors.TransportError),
        (errors.AdapterError, errors.HLSignalsError),
        (errors.NonPerpFillError, errors.AdapterError),
        (errors.ConfigError, errors.HLSignalsError),
        (errors.SourceError, errors.HLSignalsError),
        (errors.LookAheadError, errors.HLSignalsError),
    ],
)
def test_hierarchy(child: type[Exception], parent: type[Exception]) -> None:
    assert issubclass(child, parent)


def test_transport_error_carries_status() -> None:
    assert errors.RetryableError("x", status=503).status == 503
    assert errors.NonRetryableError("x").status is None

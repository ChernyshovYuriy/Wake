"""Typed exception hierarchy. Every error raised by hlsignals derives from HLSignalsError."""

from __future__ import annotations


class HLSignalsError(Exception):
    """Base class for all project errors."""


class TransportError(HLSignalsError):
    """A request to an external service failed."""


class RetryableError(TransportError):
    """Transient failure (timeout, 5xx, 429): safe to retry."""


class NonRetryableError(TransportError):
    """Permanent failure (4xx other than 429): retrying cannot help."""


class AdapterError(HLSignalsError):
    """An external payload has an unknown or invalid shape."""


class NonPerpFillError(AdapterError):
    """A fill with a known non-perp ``dir`` (spot, outcome) reached perp-only logic."""


class ConfigError(HLSignalsError):
    """Configuration is missing or invalid."""


class SourceError(HLSignalsError):
    """A wallet source failed (or all sources failed)."""


class LookAheadError(HLSignalsError):
    """A backtest tried to read data from after its as-of time."""

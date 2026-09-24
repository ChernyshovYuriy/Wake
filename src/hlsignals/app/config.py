"""Settings: the default value of every threshold, weight, window and endpoint
(CLAUDE.md rule 6), and loading overrides from TOML.

Defaults are starting points to be tuned in walk-forward testing, not claims of
optimality. ``load_settings`` overlays a TOML file on the defaults: unknown keys and
wrongly typed values raise ConfigError naming the field. Secrets never come from the file,
only from the environment (wallet sources read their API keys from it).
"""

from __future__ import annotations

import dataclasses
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, get_args, get_origin, get_type_hints

from hlsignals.core.errors import ConfigError

WALLET_FILTER_NAMES: Final = ("min_sample", "maker_profile", "inactivity", "reversal_bait")


@dataclass(frozen=True, slots=True)
class ApiSettings:
    info_url: str = "https://api.hyperliquid.xyz/info"
    ws_url: str = "wss://api.hyperliquid.xyz/ws"
    timeout_s: float = 30.0
    # docs/api-notes.md §8: 1200 weight per minute per IP.
    rate_budget: int = 1200
    rate_window_s: float = 60.0
    default_weight: int = 20
    weights: MappingProxyType[str, int] = field(
        default_factory=lambda: MappingProxyType(
            {"l2Book": 2, "clearinghouseState": 2, "allMids": 2}
        )
    )
    items_per_extra_weight: MappingProxyType[str, int] = field(
        default_factory=lambda: MappingProxyType({"userFillsByTime": 20, "candleSnapshot": 60})
    )
    retry_attempts: int = 5
    retry_base_delay_s: float = 1.0
    retry_multiplier: float = 2.0
    retry_max_delay_s: float = 30.0
    cache_ttl_s: float = 30.0
    # docs/api-notes.md §5
    fills_page_cap: int = 2000
    fills_history_cap: int = 10_000


@dataclass(frozen=True, slots=True)
class UniverseSettings:
    instruments_path: str = "config/instruments.toml"
    include_classes: frozenset[str] = frozenset({"equity_us"})
    dex_preference: tuple[str, ...] = ("xyz", "para", "io", "mkts")
    min_day_volume_usd: float = 1_000_000.0
    min_open_interest_usd: float = 250_000.0
    watchlist: frozenset[str] = frozenset()  # optional: restrict to these coins


@dataclass(frozen=True, slots=True)
class WalletFilterSettings:
    order: tuple[str, ...] = WALLET_FILTER_NAMES
    min_round_trips: int = 10
    min_taker_ratio: float = 0.2
    max_fills_per_day: float = 200.0
    min_median_hold_days: float = 1 / 24  # one hour
    max_days_since_last_fill: float = 14.0
    bait_window_hours: float = 24.0
    bait_min_events: int = 5
    bait_max_reversal_rate: float = 0.6
    bait_min_move: float = 0.02


@dataclass(frozen=True, slots=True)
class ScoringSettings:
    weights: MappingProxyType[str, float] = field(
        default_factory=lambda: MappingProxyType(
            {
                "hit_rate": 1.0,
                "drawdown": 1.0,
                "consistency": 1.0,
                "horizon_fit": 1.0,
                "prior_score": 0.5,
            }
        )
    )
    shrinkage_k: float = 10.0
    half_life_days: float = 21.0
    swing_horizon_days: float = 5.0
    wilson_z: float = 1.96
    max_tolerated_drawdown: float = 0.5  # sum of trip returns
    consistency_period_days: float = 7.0
    prior_score_scale: float = 100.0  # Copy Scores are 0-100


@dataclass(frozen=True, slots=True)
class SignalSettings:
    weights: MappingProxyType[str, float] = field(
        default_factory=lambda: MappingProxyType({"tilt": 1.0, "flow": 1.0, "overnight": 0.5})
    )
    flow_window_hours: float = 24.0
    min_flow_oi_frac: float = 0.02
    flow_full_scale_oi_frac: float = 0.10  # flow of 10% of OI -> full-strength component
    min_overnight: float = 0.003
    overnight_full_scale: float = 0.03  # a 3% overnight move -> full-strength component
    max_overnight_staleness_hours: float = 2.0
    min_wallets: int = 3
    min_trust: float = 0.4
    epsilon: float = 0.05  # on the normalized [-1, 1] score
    thin_volume_usd: float = 5_000_000.0
    weak_confidence: float = 0.5


@dataclass(frozen=True, slots=True)
class HistorySettings:
    """How much wallet history is fetched and scored, and the candle resolution."""

    lookback_days: float = 90.0
    candle_interval: str = "1h"
    signal_candle_hours: float = 120.0  # covers a long weekend for the overnight reference


@dataclass(frozen=True, slots=True)
class WalletSourceSettings:
    sources: tuple[Mapping[str, Any], ...] = (
        MappingProxyType({"type": "curated", "path": "config/wallets.toml"}),
        MappingProxyType({"type": "census", "min_observations": 20}),
    )
    precedence: tuple[str, ...] = ("curated", "census")


@dataclass(frozen=True, slots=True)
class CalendarSettings:
    path: str = "config/us_market_calendar.toml"


@dataclass(frozen=True, slots=True)
class CensusSettings:
    db_path: str = "data/census.sqlite"
    dedupe_capacity: int = 100_000
    recv_timeout_s: float = 20.0
    reconnect_delay_s: float = 5.0


@dataclass(frozen=True, slots=True)
class BacktestSettings:
    horizon_sessions: int = 5  # hold from the entry open to the close of the 5th session
    cost_bps_per_side: float = 5.0  # spread + slippage per side
    preopen_minutes: float = 30.0  # signals are built this long before each open
    train_sessions: int = 60
    test_sessions: int = 20
    min_trades_for_verdict: int = 30
    # Walk-forward grid: signal parameters tuned on each train window.
    min_trust_grid: tuple[float, ...] = (0.2, 0.3, 0.4)
    epsilon_grid: tuple[float, ...] = (0.05, 0.1)
    # HIP-3 coin -> cash ticker where they differ (trade.xyz spec: PURRDAT is Nasdaq PURR).
    ticker_overrides: MappingProxyType[str, str] = field(
        default_factory=lambda: MappingProxyType({"PURRDAT": "PURR"})
    )
    prices_timeout_s: float = 30.0


@dataclass(frozen=True, slots=True)
class DiscoverySettings:
    """Weekly vetting of census wallets into a shortlist the daily run follows."""

    min_observations: int = 20  # census trades seen before a wallet is worth vetting
    max_wallets_per_run: int = 300  # bounds a run's API time (vetting is the slow part)
    revet_days: float = 28.0  # rejected wallets get another look after this long
    state_path: str = "data/discovery.sqlite"
    shortlist_path: str = "data/discovered_wallets.toml"


@dataclass(frozen=True, slots=True)
class ReportSettings:
    format: str = "table"


@dataclass(frozen=True, slots=True)
class Settings:
    api: ApiSettings = field(default_factory=ApiSettings)
    universe: UniverseSettings = field(default_factory=UniverseSettings)
    wallet_filters: WalletFilterSettings = field(default_factory=WalletFilterSettings)
    scoring: ScoringSettings = field(default_factory=ScoringSettings)
    signals: SignalSettings = field(default_factory=SignalSettings)
    history: HistorySettings = field(default_factory=HistorySettings)
    wallet_sources: WalletSourceSettings = field(default_factory=WalletSourceSettings)
    calendar: CalendarSettings = field(default_factory=CalendarSettings)
    census: CensusSettings = field(default_factory=CensusSettings)
    report: ReportSettings = field(default_factory=ReportSettings)
    backtest: BacktestSettings = field(default_factory=BacktestSettings)
    discovery: DiscoverySettings = field(default_factory=DiscoverySettings)


# TOML table path -> Settings field. [wallets] holds sources/precedence and two sub-tables.
_SECTIONS: Final = {
    ("api",): "api",
    ("universe",): "universe",
    ("wallets", "filters"): "wallet_filters",
    ("wallets", "scoring"): "scoring",
    ("signals",): "signals",
    ("history",): "history",
    ("calendar",): "calendar",
    ("census",): "census",
    ("report",): "report",
    ("backtest",): "backtest",
    ("discovery",): "discovery",
}
_SECRET_MARKERS: Final = ("api_key", "apikey", "token", "secret", "password")


def load_settings(path: Path | None) -> Settings:
    """Defaults, overlaid with the TOML file at ``path`` when given."""
    if path is None:
        return Settings()
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    return settings_from_mapping(raw)


def settings_from_mapping(raw: Mapping[str, Any]) -> Settings:
    _reject_secrets(raw, "")
    wallets = dict(raw.get("wallets", {}))
    known_top = {path[0] for path in _SECTIONS}
    unknown = set(raw) - known_top
    if unknown:
        raise ConfigError(f"unknown config section(s): {sorted(unknown)}")
    overrides: dict[str, Any] = {}
    for path, attr in _SECTIONS.items():
        table: Any = raw
        for part in path:
            table = table.get(part, {}) if isinstance(table, Mapping) else {}
        if table:
            current = getattr(Settings(), attr)
            overrides[attr] = _section(type(current), table, ".".join(path))
    source_keys = {k: wallets.pop(k) for k in ("sources", "precedence") if k in wallets}
    wallets.pop("filters", None)
    wallets.pop("scoring", None)
    if wallets:
        raise ConfigError(f"unknown key(s) in [wallets]: {sorted(wallets)}")
    if source_keys:
        overrides["wallet_sources"] = _section(WalletSourceSettings, source_keys, "wallets")
    return dataclasses.replace(Settings(), **overrides)


def _reject_secrets(raw: Any, where: str) -> None:
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            name = f"{where}.{key}" if where else key
            if any(marker in key.lower() for marker in _SECRET_MARKERS):
                raise ConfigError(f"{name}: secrets must come from environment variables")
            _reject_secrets(value, name)
    elif isinstance(raw, list):
        for item in raw:
            _reject_secrets(item, where)


def _section[T](cls: type[T], raw: Any, where: str) -> T:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"[{where}] must be a table")
    hints = get_type_hints(cls)
    unknown = set(raw) - set(hints)
    if unknown:
        raise ConfigError(f"unknown key(s) in [{where}]: {sorted(unknown)}")
    values = {key: _coerce(hints[key], value, f"{where}.{key}") for key, value in raw.items()}
    return dataclasses.replace(cls(), **values)  # type: ignore[type-var]


def _coerce(hint: Any, value: Any, name: str) -> Any:
    origin, args = get_origin(hint), get_args(hint)
    if hint is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ConfigError(f"{name} must be a number, got {value!r}")
        return float(value)
    if hint in (int, str, bool):
        if isinstance(value, bool) != (hint is bool) or not isinstance(value, hint):
            raise ConfigError(f"{name} must be {hint.__name__}, got {value!r}")
        return value
    if origin in (tuple, frozenset):
        if not isinstance(value, list):
            raise ConfigError(f"{name} must be a list, got {value!r}")
        items = [_coerce(args[0], item, f"{name}[]") for item in value]
        return origin(items)
    if origin is MappingProxyType:
        if not isinstance(value, Mapping):
            raise ConfigError(f"{name} must be a table, got {value!r}")
        return MappingProxyType({k: _coerce(args[1], v, f"{name}.{k}") for k, v in value.items()})
    if origin is Mapping:  # free-form tables (wallet source specs)
        if not isinstance(value, Mapping):
            raise ConfigError(f"{name} must be a table, got {value!r}")
        return MappingProxyType(dict(value))
    raise ConfigError(f"{name}: unsupported setting type {hint!r}")  # pragma: no cover

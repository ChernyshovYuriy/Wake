"""Default values of every threshold, weight, window and endpoint (CLAUDE.md rule 6).

These are starting points to be tuned in walk-forward testing, not claims of optimality.
Phase 8 adds loading overrides from TOML with validation; until then these are used as-is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

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
class VetSettings:
    lookback_days: float = 90.0
    candle_interval: str = "1h"


@dataclass(frozen=True, slots=True)
class Settings:
    api: ApiSettings = field(default_factory=ApiSettings)
    universe: UniverseSettings = field(default_factory=UniverseSettings)
    wallet_filters: WalletFilterSettings = field(default_factory=WalletFilterSettings)
    scoring: ScoringSettings = field(default_factory=ScoringSettings)
    signals: SignalSettings = field(default_factory=SignalSettings)
    vet: VetSettings = field(default_factory=VetSettings)

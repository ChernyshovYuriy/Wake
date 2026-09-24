"""Builds collaborators from Settings (Factory Method registries keyed by config names)."""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path

from hlsignals.app.config import ApiSettings, ScoringSettings, WalletFilterSettings
from hlsignals.core.chain import Filter, FilterChain
from hlsignals.core.clock import Clock, Sleeper
from hlsignals.core.errors import ConfigError
from hlsignals.domain.symbols import Symbol
from hlsignals.infra.gateway import HyperliquidGateway
from hlsignals.infra.pagination import FillPaging
from hlsignals.infra.transport import (
    CachingTransport,
    HttpTransport,
    RateLimitedTransport,
    RetryingTransport,
    RetryPolicy,
    WeightTable,
)
from hlsignals.universe.instruments import InstrumentCatalog
from hlsignals.wallets.filters import (
    InactivityFilter,
    MakerProfileFilter,
    MinEquitySampleFilter,
    ReversalBaitFilter,
    wallet_filter_chain,
)
from hlsignals.wallets.scoring.features import (
    ConsistencyFeature,
    DrawdownFeature,
    HitRateFeature,
    HorizonFitFeature,
    PriorScoreFeature,
    WalletFeature,
)
from hlsignals.wallets.scoring.scorer import WalletScorer, WeightedFeature
from hlsignals.wallets.scoring.slice import EquitySlice

_FILTERS: Mapping[str, Callable[[WalletFilterSettings], Filter[EquitySlice]]] = {
    "min_sample": lambda s: MinEquitySampleFilter(s.min_round_trips),
    "maker_profile": lambda s: MakerProfileFilter(
        s.min_taker_ratio, s.max_fills_per_day, s.min_median_hold_days
    ),
    "inactivity": lambda s: InactivityFilter(s.max_days_since_last_fill),
    "reversal_bait": lambda s: ReversalBaitFilter(
        s.bait_window_hours, s.bait_min_events, s.bait_max_reversal_rate, s.bait_min_move
    ),
}

_FEATURES: Mapping[str, Callable[[ScoringSettings], WalletFeature]] = {
    "hit_rate": lambda s: HitRateFeature(s.wilson_z),
    "drawdown": lambda s: DrawdownFeature(s.max_tolerated_drawdown),
    "consistency": lambda s: ConsistencyFeature(s.consistency_period_days),
    "horizon_fit": lambda s: HorizonFitFeature(s.swing_horizon_days),
    "prior_score": lambda s: PriorScoreFeature(s.prior_score_scale),
}


def build_wallet_filter_chain(settings: WalletFilterSettings) -> FilterChain[EquitySlice]:
    unknown = [name for name in settings.order if name not in _FILTERS]
    if unknown:
        raise ConfigError(f"unknown wallet filter(s) {unknown}; valid: {sorted(_FILTERS)}")
    return wallet_filter_chain([_FILTERS[name](settings) for name in settings.order])


def build_wallet_scorer(settings: ScoringSettings) -> WalletScorer:
    unknown = set(settings.weights) - set(_FEATURES)
    if unknown:
        raise ConfigError(
            f"unknown wallet feature(s) {sorted(unknown)}; valid: {sorted(_FEATURES)}"
        )
    features = [
        WeightedFeature(_FEATURES[name](settings), weight)
        for name, weight in settings.weights.items()
    ]
    return WalletScorer(features, settings.shrinkage_k, settings.half_life_days)


def build_gateway(settings: ApiSettings, clock: Clock, sleeper: Sleeper) -> HyperliquidGateway:
    """The live transport stack: cache(retry(rate limit(http))).

    Cache outermost so hits cost no rate budget; retry outside the rate limiter so every
    attempt (including ones answered with 429) is charged.
    """
    http = HttpTransport(settings.info_url, settings.timeout_s)
    weights = WeightTable(
        settings.default_weight, settings.weights, settings.items_per_extra_weight
    )
    limited = RateLimitedTransport(
        http,
        budget=settings.rate_budget,
        window_s=settings.rate_window_s,
        weights=weights,
        clock=clock,
        sleeper=sleeper,
    )
    policy = RetryPolicy(
        settings.retry_attempts,
        settings.retry_base_delay_s,
        settings.retry_multiplier,
        settings.retry_max_delay_s,
    )
    paging = FillPaging(settings.fills_page_cap, settings.fills_history_cap)
    retrying = RetryingTransport(limited, policy, sleeper)
    cached = CachingTransport(retrying, settings.cache_ttl_s, clock)
    return HyperliquidGateway(cached, paging)


def load_catalog(path: Path) -> InstrumentCatalog:
    try:
        return InstrumentCatalog.from_mapping(tomllib.loads(path.read_text()))
    except FileNotFoundError as exc:
        raise ConfigError(f"instrument catalog not found: {path}") from exc


def load_equities(catalog: InstrumentCatalog, include: frozenset[str]) -> frozenset[Symbol]:
    catalog.check_classes(include)
    return catalog.symbols_in(include)

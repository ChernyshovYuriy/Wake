"""Builds collaborators from Settings (Factory Method registries keyed by config names)."""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path

from hlsignals.app.config import (
    ApiSettings,
    ScoringSettings,
    Settings,
    SignalSettings,
    UniverseSettings,
    WalletFilterSettings,
)
from hlsignals.core.chain import Filter, FilterChain
from hlsignals.core.clock import Clock, Sleeper
from hlsignals.core.errors import ConfigError
from hlsignals.domain.models import MarketCtx
from hlsignals.domain.symbols import Symbol
from hlsignals.infra.gateway import HyperliquidGateway
from hlsignals.infra.pagination import FillPaging
from hlsignals.infra.transport import (
    CachingTransport,
    HttpTransport,
    RateLimitedTransport,
    RetryingTransport,
    RetryPolicy,
    Transport,
    WeightTable,
)
from hlsignals.reporting.renderers import REPORT_FORMATS
from hlsignals.session.calendar import UsEquityCalendar, calendar_from_mapping
from hlsignals.signals.combiner import WeightedCombiner
from hlsignals.signals.corroboration import Corroboration
from hlsignals.signals.engine import FlagThresholds, SignalEngine
from hlsignals.signals.features import (
    FlowFeature,
    OvernightFeature,
    PositioningFeature,
    SignalFeature,
)
from hlsignals.universe.instruments import InstrumentCatalog
from hlsignals.universe.market_filters import (
    DelistedFilter,
    MinDayVolumeFilter,
    MinOpenInterestFilter,
    WatchlistFilter,
    market_filter_chain,
)
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
    try:
        return wallet_filter_chain([_FILTERS[name](settings) for name in settings.order])
    except ValueError as exc:
        raise ConfigError(f"invalid [wallets.filters] settings: {exc}") from exc


def build_wallet_scorer(settings: ScoringSettings) -> WalletScorer:
    unknown = set(settings.weights) - set(_FEATURES)
    if unknown:
        raise ConfigError(
            f"unknown wallet feature(s) {sorted(unknown)}; valid: {sorted(_FEATURES)}"
        )
    try:
        features = [
            WeightedFeature(_FEATURES[name](settings), weight)
            for name, weight in settings.weights.items()
        ]
        return WalletScorer(features, settings.shrinkage_k, settings.half_life_days)
    except ValueError as exc:
        raise ConfigError(f"invalid [wallets.scoring] settings: {exc}") from exc


def build_gateway(settings: ApiSettings, clock: Clock, sleeper: Sleeper) -> HyperliquidGateway:
    return gateway_for(build_transport(settings, clock, sleeper), settings)


def gateway_for(transport: Transport, settings: ApiSettings) -> HyperliquidGateway:
    paging = FillPaging(settings.fills_page_cap, settings.fills_history_cap)
    return HyperliquidGateway(transport, paging)


def build_transport(settings: ApiSettings, clock: Clock, sleeper: Sleeper) -> Transport:
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
    retrying = RetryingTransport(limited, policy, sleeper)
    return CachingTransport(retrying, settings.cache_ttl_s, clock)


def load_calendar(path: Path) -> UsEquityCalendar:
    try:
        return calendar_from_mapping(tomllib.loads(path.read_text()))
    except FileNotFoundError as exc:
        raise ConfigError(f"market calendar not found: {path}") from exc


def load_catalog(path: Path) -> InstrumentCatalog:
    try:
        return InstrumentCatalog.from_mapping(tomllib.loads(path.read_text()))
    except FileNotFoundError as exc:
        raise ConfigError(f"instrument catalog not found: {path}") from exc


def load_equities(catalog: InstrumentCatalog, include: frozenset[str]) -> frozenset[Symbol]:
    catalog.check_classes(include)
    return catalog.symbols_in(include)


def build_signal_engine(settings: SignalSettings) -> SignalEngine:
    features: list[SignalFeature] = [
        PositioningFeature(),
        FlowFeature(
            settings.flow_window_hours, settings.min_flow_oi_frac, settings.flow_full_scale_oi_frac
        ),
        OvernightFeature(
            settings.min_overnight,
            settings.overnight_full_scale,
            settings.max_overnight_staleness_hours,
        ),
    ]
    try:
        return SignalEngine(
            features,
            Corroboration(settings.min_wallets, settings.min_trust),
            WeightedCombiner(settings.weights, settings.epsilon),
            FlagThresholds(settings.thin_volume_usd, settings.weak_confidence),
        )
    except ValueError as exc:
        raise ConfigError(f"invalid [signals] settings: {exc}") from exc


def build_market_filter_chain(settings: UniverseSettings) -> FilterChain[MarketCtx]:
    try:
        filters: list[Filter[MarketCtx]] = [
            DelistedFilter(),
            MinDayVolumeFilter(settings.min_day_volume_usd),
            MinOpenInterestFilter(settings.min_open_interest_usd),
        ]
    except ValueError as exc:
        raise ConfigError(f"invalid [universe] settings: {exc}") from exc
    if settings.watchlist:
        filters.append(WatchlistFilter(settings.watchlist))
    return market_filter_chain(filters)


def validate_settings(settings: Settings) -> None:
    """Build every configurable component once so bad values fail at load time."""
    build_wallet_filter_chain(settings.wallet_filters)
    build_wallet_scorer(settings.scoring)
    build_signal_engine(settings.signals)
    build_market_filter_chain(settings.universe)
    if settings.report.format not in REPORT_FORMATS:
        raise ConfigError(
            f"report.format must be one of {sorted(REPORT_FORMATS)}: {settings.report.format!r}"
        )
    if not settings.wallet_sources.sources:
        raise ConfigError("[wallets] needs at least one entry in sources")

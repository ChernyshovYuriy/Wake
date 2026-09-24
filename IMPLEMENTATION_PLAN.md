# hl-whale-signals — Implementation Plan for Claude Code

> Swing-trade **signal generator** for liquid US equities, sourced from Hyperliquid
> HIP-3 equity perps (smart-money positioning, smart-money flow, overnight perp move).
> **Signals only. No order execution, no broker integration.** The user trades manually.

---

## 0. How to use this plan (instructions to Claude Code)

1. Work **phase by phase** (Section 9). Do not start a phase until the previous phase's
   Definition of Done is green.
2. **Test-first.** For every module: write the unit tests from Section 8 first, watch them
   fail, then implement.
3. Each phase ends with: `pytest` green, coverage gate met, `ruff` clean, `mypy --strict`
   clean, `lint-imports` clean, duplication check clean. Commit once per phase.
4. When the plan says **VERIFY**, the fact comes from third-party docs and was not checked
   against a live response. Resolve it in Phase 0 from captured fixtures and record the
   answer in `docs/api-notes.md`. Never guess; if a fixture contradicts this plan, the
   fixture wins and this plan's assumption is updated in `docs/api-notes.md`.
5. Copy Section 2 verbatim into the repo's `CLAUDE.md`.

---

## 1. Scope

**In scope**
- Discover the HIP-3 equity perp universe and its liquidity at runtime.
- Source "smart money" wallets from pluggable sources (curated, paid API, DIY census).
- Score wallets on their **equity-perp activity only**.
- Compute per-ticker signals: positioning tilt, flow (normalized by OI), overnight perp move.
- Require corroboration across independent wallets.
- Emit a ranked report where every number is traceable to evidence.
- Backtest / walk-forward harness to test whether signals beat buy-and-hold.

**Out of scope**
- Placing orders anywhere (Hyperliquid `/exchange`, brokers).
- Streaming intraday alerting beyond the census recorder (optional later).
- Crypto signals. Crypto fills are excluded from scoring and signals.

---

## 2. Engineering rules (copy into `CLAUDE.md`)

1. **Single home per logic.** Every distinguished piece of logic lives in exactly one
   module (see the Single-Home Registry, Section 5). Other code calls it; it never
   re-implements it. If you need it somewhere new, import it; if the import direction is
   illegal, the logic is in the wrong layer — move it, don't copy it.
2. **No copy-paste**, including in tests. Shared test behavior goes into fixtures,
   factories (`tests/factories.py`), or parametrized contract suites.
3. **GoF patterns only where they solve a named problem** (Section 4). No pattern for its
   own sake. **No Singletons** — all collaborators are injected.
4. **Pure core, I/O at the edge.** `domain`, `core`, `signals`, `wallets/scoring`,
   `wallets/filters`, `session` perform no I/O and read no wall-clock time.
5. **Time is injected.** Only `core/clock.py` knows how to get "now". Nothing calls
   `datetime.now()` / `time.time()` / `time.sleep()` directly.
6. **No magic numbers.** Every threshold, weight, window and floor comes from config
   (`app/config.py` defaults), never literals in logic.
7. **Evidence-only outputs.** Every score carries the raw inputs that produced it.
   No field in the report may be unexplained.
8. **No network in unit tests.** Use `FixtureTransport`. Live tests are marked
   `@pytest.mark.live` and are excluded by default.
9. **Typed everything.** `mypy --strict`. Domain models are frozen dataclasses.
10. **Fail loudly on unknown API shapes.** Unknown enum values / missing required fields
    raise typed errors; they are never silently coerced.

---

## 3. Architecture

### 3.1 Layers and dependency rule

```
app  ──►  reporting, backtest, signals, wallets, universe, session, infra
backtest ──► signals, wallets, session, domain, core
signals ──► domain, core
wallets ──► domain, core            (sources may use infra via injected ports)
universe ──► domain, core
session ──► core
infra ──► domain, core
domain ──► core
core ──► (stdlib only)
```

Enforced by `import-linter` contracts (Phase 1). `signals` and `wallets/scoring` must
never import `infra`.

### 3.2 Package layout

```
hl-whale-signals/
├── pyproject.toml
├── CLAUDE.md
├── .importlinter
├── docs/api-notes.md                 # Phase 0 findings (VERIFY answers)
├── config/example.toml
├── src/hlsignals/
│   ├── core/
│   │   ├── clock.py                  # Clock port, SystemClock, FakeClock, Sleeper
│   │   ├── mathx.py                  # clamp, safe_div, wilson_lower_bound, half_life_decay, shrinkage
│   │   └── errors.py                 # typed exception hierarchy
│   ├── domain/
│   │   ├── models.py                 # Fill, Position, Candle, MarketCtx, WalletRecord,
│   │   │                             # ScoredWallet, FeatureValue, TickerSignal, SignalReport
│   │   ├── symbols.py                # Symbol value object: parse/format "dex:coin"
│   │   ├── direction.py              # fill dir/side → signed notional (the ONLY place)
│   │   └── lots.py                   # FIFO lot matcher: holding time, realized PnL, as-of position
│   ├── infra/
│   │   ├── transport.py              # Transport ABC + HttpTransport + decorators + FixtureTransport
│   │   ├── gateway.py                # HyperliquidGateway facade (one method per info type)
│   │   ├── adapters.py               # raw JSON → domain models
│   │   └── pagination.py             # FillsIterator over userFillsByTime pages
│   ├── session/
│   │   └── calendar.py               # SessionCalendar strategy + HolidayProvider port
│   ├── universe/
│   │   ├── discovery.py              # EquityDexLocator
│   │   └── market_filters.py         # chain: delisted, min volume, min OI
│   ├── wallets/
│   │   ├── sources/
│   │   │   ├── base.py               # WalletSource (Template Method)
│   │   │   ├── curated.py
│   │   │   ├── nansen.py             # paid (optional)
│   │   │   ├── apify.py              # paid (optional)
│   │   │   ├── census.py
│   │   │   ├── composite.py
│   │   │   └── factory.py            # SourceFactory (registry of source types)
│   │   ├── census/
│   │   │   ├── tape.py               # TapeSubject (Observer subject)
│   │   │   ├── recorder.py           # CensusRecorder (observer)
│   │   │   └── registry.py           # WalletRegistry port + SqliteRegistry
│   │   ├── filters.py                # Chain of Responsibility wallet filters
│   │   └── scoring/
│   │       ├── features.py           # WalletFeature strategies
│   │       ├── slice.py              # EquitySlice: restricts a wallet's history to equity coins
│   │       └── scorer.py             # WalletScorer (Composite of features + confidence + decay)
│   ├── signals/
│   │   ├── features.py               # Positioning / Flow / Overnight feature strategies
│   │   ├── corroboration.py
│   │   ├── combiner.py               # WeightedCombiner
│   │   └── ranker.py
│   ├── reporting/
│   │   ├── evidence.py               # evidence formatting helpers
│   │   └── renderers.py              # Table / Markdown / JSON renderer strategies
│   ├── backtest/
│   │   ├── asof.py                   # AsOfView — the ONLY look-ahead guard
│   │   ├── prices.py                 # PriceHistory port + adapters (stock daily bars)
│   │   ├── replay.py
│   │   ├── walkforward.py
│   │   ├── costs.py
│   │   └── metrics.py
│   └── app/
│       ├── config.py                 # typed config, defaults, validation, env overrides
│       ├── builder.py                # PipelineBuilder
│       ├── pipeline.py               # SignalPipeline (orchestration only, no logic)
│       └── cli.py                    # run | vet | census | backtest | capture-fixtures
└── tests/
    ├── conftest.py
    ├── factories.py                  # builders for domain objects (no duplicated setup)
    ├── fixtures/hl/                  # captured JSON from Phase 0
    ├── contracts/                    # abstract suites run against every implementation
    └── <mirror of src tree>
```

---

## 4. GoF pattern map

| Pattern | Where | Problem it solves |
|---|---|---|
| **Decorator** | `infra/transport.py`: `RetryingTransport`, `RateLimitedTransport`, `CachingTransport` wrapping `HttpTransport` | Retry, rate limiting and caching are orthogonal concerns stacked in any order without subclass explosion. |
| **Facade** | `infra/gateway.py`: `HyperliquidGateway` | One typed method per `/info` request type hides payload construction and adaptation. |
| **Adapter** | `infra/adapters.py` (HL JSON → domain); each `WalletSource` (curated TOML / Nansen / Apify / registry → `WalletRecord`); `backtest/prices.py` (price vendor → `Candle`) | External shapes are converted in one place into internal models. |
| **Iterator** | `infra/pagination.py`: `FillsIterator` | `userFillsByTime` is paged (VERIFY page cap); callers iterate fills without knowing paging. |
| **Template Method** | `wallets/sources/base.py`: `WalletSource.fetch()` = `_load_raw()` → `_adapt()` → `_validate()` (shared) → `_dedupe()` (shared) | All sources share validation/dedup; subclasses supply only loading and adapting. |
| **Strategy** | Wallet sources; `WalletFeature`s; signal features; `SessionCalendar`; renderers; cost models | Interchangeable algorithms selected by config. |
| **Composite** | `CompositeWalletSource` (blends sources, isolates failures); `WalletScorer` treating a set of `WalletFeature`s as one scorer | Clients treat one or many uniformly. |
| **Chain of Responsibility** | `wallets/filters.py` (min sample, maker/HFT profile, inactivity, reversal-bait); `universe/market_filters.py` | Ordered, independently testable accept/reject steps, each recording its reason. |
| **Observer** | `wallets/census/tape.py` subject; `CensusRecorder` observer (future: `LargePrintAlerter`) | Tape events fan out to independent consumers. |
| **Factory Method** | `wallets/sources/factory.py`, filter/feature factories keyed by config `type` | Config-driven construction without `if/elif` chains scattered across the app. |
| **Builder** | `app/builder.py`: `PipelineBuilder` | Stepwise assembly of a pipeline from validated config, with fail-fast on missing parts. |

Explicitly **not** used: Singleton (use DI), Visitor (renderers are simple Strategies).

---

## 5. Single-Home Registry

Each row is a piece of logic that must exist **exactly once**. Anything else calling it
imports from the listed module.

| Logic | Single home |
|---|---|
| Symbol parse/format `"<dex>:<coin>"`, case normalization, equity-vs-crypto test | `domain/symbols.py` |
| Fill `dir`/`side` → signed notional (+ long exposure / − short exposure) | `domain/direction.py` |
| FIFO lot matching, holding time, realized PnL fallback, as-of net position from fills | `domain/lots.py` |
| "Now", ms⇄datetime conversion, sleeping | `core/clock.py` |
| clamp, safe division, Wilson lower bound, sample shrinkage, half-life decay | `core/mathx.py` |
| Last cash close / next open / is-open / half-days / holidays | `session/calendar.py` |
| Restricting a wallet's fills/positions to equity coins | `wallets/scoring/slice.py` |
| Wallet trust + confidence + decay formula | `wallets/scoring/scorer.py` |
| Corroboration rule (≥ N independent trusted wallets) | `signals/corroboration.py` |
| Signal weighting and direction decision | `signals/combiner.py` |
| Look-ahead prevention in backtests | `backtest/asof.py` |
| Default values of every threshold/weight | `app/config.py` |
| Retry/backoff policy | `infra/transport.py` (`RetryingTransport`) |
| `/info` payload shapes | `infra/gateway.py` |

**Enforcement (Phase 1):**
- `import-linter` layer contracts.
- `tests/architecture/test_single_home.py`: AST scan asserting e.g. only `symbols.py`
  builds strings with `":"` from dex+coin, only `clock.py` references `datetime.now` /
  `time.time` / `time.sleep`, only `direction.py` maps `dir` literals.
- Duplication gate: `pylint --disable=all --enable=duplicate-code --min-similarity-lines=6`
  (or `jscpd`) over `src/` and `tests/`.

---

## 6. Component specifications

### 6.1 core
- `Clock` protocol: `now() -> datetime` (tz-aware UTC). `SystemClock`, `FakeClock(t)` with
  `advance(delta)`. `Sleeper` protocol: `sleep(seconds)`; `FakeSleeper` records calls.
  `to_ms(dt)`, `from_ms(ms)` reject naive datetimes.
- `mathx`: `clamp(x, lo, hi)`, `safe_div(a, b, default)`, `wilson_lower_bound(wins, n, z)`,
  `shrinkage(n, k) = n/(n+k)`, `half_life_decay(age_days, half_life_days)`. All reject NaN/inf.
- `errors`: `HLSignalsError` → `TransportError` (`RetryableError`, `NonRetryableError`),
  `AdapterError`, `ConfigError`, `SourceError`, `LookAheadError`.

### 6.2 domain
- Frozen dataclasses. `Fill(wallet, symbol, px, sz, side, dir, time_ms, tid, crossed,
  closed_pnl, fee)` — field names VERIFY in Phase 0 (`crossed` = taker flag, `closedPnl`,
  `tid`, `hash`).
- `Symbol(dex: str | None, coin: str)` with `parse(str)`, `__str__`, `is_equity`.
- `direction.signed_notional(fill) -> float`: exhaustive mapping table built from Phase 0
  fixtures (e.g. Open Long +, Close Long −, Open Short −, Close Short +, flips such as
  `"Long > Short"` VERIFY). Unknown value → `AdapterError`.
- `lots.LotBook`: feed fills in (time, tid) order; outputs closed lots with holding time and
  PnL; `net_position(as_of_ms)`; orphan closes (history truncated) are flagged, not guessed.

### 6.3 infra
- `Transport.post(payload) -> Any`. `HttpTransport(url, timeout)`.
- `RetryingTransport(inner, policy, sleeper)`: retries `RetryableError` (timeouts, 5xx, 429)
  with exponential backoff; never retries 4xx other than 429.
- `RateLimitedTransport(inner, budget, clock, sleeper)`: weight-based budget per window
  (weights per request type from config; VERIFY current HL limits).
- `CachingTransport(inner, ttl, clock)`: key = canonical JSON of payload; errors not cached.
- `FixtureTransport(dir)`: serves captured JSON by payload key; unknown payload → error.
- `HyperliquidGateway` methods: `perp_dexs()`, `meta_and_ctxs(dex)`,
  `clearinghouse_state(user, dex)`, `user_fills_by_time(user, start, end)` (returns
  `FillsIterator`), `candle_snapshot(symbol, interval, start, end)`, `l2_book(symbol)`.
- `FillsIterator`: pages by advancing `startTime` past the last fill; de-duplicates on
  `(tid, hash)` at page boundaries; stops on short page; exposes `truncated` if the API's
  historical cap is hit (VERIFY cap: docs indicate ~2000 per page / ~10000 most recent).

### 6.4 session
- `SessionCalendar` strategy: `last_close(t)`, `next_open(t)`, `is_open(t)`, `sessions(range)`.
- `UsEquityCalendar(holidays: HolidayProvider)` — 09:30–16:00 America/New_York, early
  closes, DST-correct. `HolidayProvider` port; default adapter backed by
  `pandas_market_calendars` (or a static table in config).
- Calendar choice per instrument class is config-driven (future commodity/FX calendars).

### 6.5 universe
- `EquityDexLocator(gateway, known_tickers, preference)`: enumerates `perpDexs`, keeps dexes
  whose universe intersects `known_tickers`, orders by configured preference.
- `load_markets(dex)` → `MarketCtx` list (markPx, prevDayPx, OI in USD, dayNtlVlm, funding).
  `meta`/`ctxs` length mismatch → `AdapterError`.
- Market filter chain: `DelistedFilter`, `MinDayVolumeFilter`, `MinOpenInterestFilter`,
  `WatchlistFilter`.

### 6.6 wallets — sources
- `WalletRecord(address, source, raw_score: float | None, raw_metric: str | None, as_of)`.
- `WalletSource.fetch() -> SourceResult(records, diagnostics)` (Template Method).
  Shared steps: address validation (`0x` + 40 hex, lower-cased), dedupe keeping the newest
  `as_of`.
- `CuratedSource(path)`: TOML list; optional Copy Score copied from Hyperdash/ASXN as
  `raw_score`.
- `NansenSource`, `ApifySource`: optional; API keys only from env vars; disabled if absent.
- `CensusSource(registry, min_observations)`: addresses the census has seen on equity coins.
- `CompositeWalletSource(sources, precedence)`: merges; one failing source is recorded in
  diagnostics and does not abort; all failing → `SourceError`.
- `SourceFactory`: `type` → constructor registry; unknown type error lists valid types.

### 6.7 wallets — census (Observer)
- `TapeSubject`: `subscribe/unsubscribe/publish(fill)`; observer exceptions are isolated
  and logged; publish order preserved.
- `CensusRecorder`: on each equity-symbol fill, upserts wallet into `WalletRegistry`
  (first_seen, last_seen, n_fills). Dedup by `tid` across reconnects.
- `WalletRegistry` port; `SqliteRegistry` adapter; round-trip persistence.
- Tape feed adapter (WS `trades`/`allFills`, VERIFY channel availability and the per-IP
  subscription cap) is an I/O edge behind the subject.

### 6.8 wallets — filters (Chain of Responsibility)
Each filter: `apply(profile) -> Verdict(accepted, reason)`; chain stops at first reject and
records the reason.
- `MinEquitySampleFilter(min_closed_lots)`
- `MakerProfileFilter(max_taker_ratio_floor, max_fills_per_day, min_median_hold)` —
  removes market-maker / HFT / arb profiles (symmetric flow, tiny holds, very high counts).
- `InactivityFilter(max_days_since_last_equity_fill)`
- `ReversalBaitFilter(window, min_events, max_precede_reversal_rate)` — flags wallets whose
  entries reliably precede reversals.

### 6.9 wallets — scoring
- `EquitySlice.from_history(fills, positions)` → only equity symbols (via `Symbol.is_equity`).
- `WalletFeature` strategy → `FeatureValue(value ∈ [0,1], evidence: dict)`:
  `HitRateFeature` (Wilson lower bound), `DrawdownFeature` (1 − normalized max DD),
  `ConsistencyFeature` (share of positive periods), `HorizonFitFeature` (median hold vs
  configured swing horizon), `PriorScoreFeature` (normalized `raw_score`, optional).
- `WalletScorer` (Composite): `trust = clamp(Σ wᵢ·featureᵢ / Σ wᵢ) · shrinkage(n, k) ·
  half_life_decay(age)`. Returns `ScoredWallet(address, trust, confidence, decay,
  features, n_closed_lots, track_record_days)`.

### 6.10 signals
- Inputs: trusted wallets' current equity positions + recent fills, `MarketCtx`, candles,
  `SessionCalendar`, clock.
- `PositioningFeature`: trust-weighted net notional, gross, `tilt = net/gross ∈ [-1,1]`,
  n_long, n_short.
- `FlowFeature`: trust-weighted signed flow in window ÷ OI (USD); zeroed below
  `min_flow_oi_frac`.
- `OvernightFeature`: perp price at `last_close` vs latest; zeroed below `min_overnight`;
  reports `ref_px`, `last_px`, `pct`, `stale` flag.
- `Corroboration(min_wallets, min_trust)`: ticker is `INSUFFICIENT` (with reason) unless
  enough distinct trusted wallets hold/trade it.
- `WeightedCombiner(weights, epsilon)`: score = Σ weight·feature; direction long/short/flat
  (|score| ≤ ε → flat). Negative weights rejected.
- `Ranker`: by |score| desc, ties broken by ticker; `INSUFFICIENT` listed after scored names.
- `TickerSignal` carries every feature's evidence plus flags: thin volume, weak sample,
  stale overnight ref, cash session currently open.

### 6.11 reporting
- Renderer strategies: `TableRenderer` (terminal), `MarkdownRenderer`, `JsonRenderer`
  (stable schema, versioned). Diagnostics section: sources used/failed, wallets
  filtered-out counts by reason, universe size after filters, `as_of`.

### 6.12 backtest
- `AsOfView(t)`: the only gateway for historical data in backtests; every accessor filters
  `time <= t`; any attempt to read beyond raises `LookAheadError`.
- Historical reconstruction: wallet positions from fills via `LotBook.net_position(as_of)`;
  perp candles via `candle_snapshot` (VERIFY historical depth cap); stock daily bars via
  `PriceHistory` adapter (e.g. yfinance/Stooq — pluggable).
- `Replay`: for each session date, build signals as of pre-open, record implied trade,
  measure forward return over configured horizon on the **real stock**.
- `CostModel` strategy: spread + slippage bps.
- `Metrics`: hit rate, mean/median forward return, Sharpe, max DD, exposure, n trades.
- `Benchmark`: buy-and-hold same names, same dates, same costs.
- `WalkForward(train, test, step)`: non-overlapping test folds; parameters tuned on train only.
- Report must state sample size and the regime covered; flag "insufficient sample" below
  configurable thresholds.

### 6.13 app
- `Config` (TOML + defaults + validation; secrets only from env).
- `PipelineBuilder`: `.with_config()`, `.with_clock()`, `.with_transport()`, `.build()`;
  missing required part → `ConfigError` naming it.
- `SignalPipeline.run(as_of)`: orchestration only — discover → filter markets → fetch
  wallets → filter → score → features → corroborate → combine → rank → report.
- CLI: `run [--asof] [--format]`, `vet` (show wallet scores & filter reasons),
  `census` (run recorder), `backtest`, `capture-fixtures` (Phase 0).
- Exit codes: 0 ok, 2 config error, 3 all sources failed, 4 API error.

---

## 7. Test strategy

- **Framework:** `pytest`, `pytest-cov`, `hypothesis` (property tests), no network.
- **Coverage gates:** branch coverage 100% for `core`, `domain`, `signals`,
  `wallets/scoring`, `wallets/filters`, `session`, `backtest/asof`; ≥ 95% overall.
- **Contract suites** (`tests/contracts/`): one abstract suite per interface, parametrized
  over every implementation — `WalletSource`, `Transport` decorators, `SessionCalendar`,
  `Renderer`, `WalletRegistry`. New implementations get tested by adding one parameter,
  never by copying tests.
- **Factories** (`tests/factories.py`): `make_fill(**overrides)`, `make_position`,
  `make_candle_series`, `make_market_ctx`, `make_scored_wallet`. All test setup goes through
  these.
- **Golden fixtures**: sanitized real responses from Phase 0; adapters are tested against
  them so field-name drift is caught.
- **Property tests**: bounds (`tilt ∈ [-1,1]`, `trust ∈ [0,1]`), monotonicity (confidence
  non-decreasing in n; decay non-increasing in age), sign symmetry (mirroring all fills
  flips signal sign), AsOfView never leaks future rows for random `t`.
- **Architecture tests**: import-linter + single-home AST scan (Section 5).

---

## 8. Unit-test matrix (use + corner cases)

### core/clock, core/mathx
- `to_ms/from_ms` round-trip; naive datetime rejected; epoch 0; far-future.
- `FakeClock.advance`; `FakeSleeper` records durations.
- `clamp` below/at/above bounds; `lo > hi` rejected; NaN rejected.
- `safe_div` b=0 → default; negative; tiny denominators.
- `wilson_lower_bound`: n=0 → 0; all wins; all losses; wins > n rejected; z variations.
- `shrinkage` n=0; k=0 rejected; large n → 1.
- `half_life_decay` age 0 → 1; age = half-life → 0.5; negative age rejected.

### domain/symbols
- `"xyz:GOOGL"` parse; bare `"BTC"` (crypto); lowercase normalized; `"xyz:"`, `":GOOGL"`,
  `"a:b:c"`, empty, whitespace → error; round-trip `str(parse(s)) == s`; `is_equity`.

### domain/direction
- Every `dir` value in the fixture-derived table → correct sign; flips; `sz=0` → 0;
  unknown `dir` → `AdapterError`; notional = px·sz (never negative before sign).

### domain/lots
- Open then full close; partial closes; scale-in/out; flip through zero; close with no
  open (orphan flagged); interleaved coins isolated; same-timestamp ordering by `tid`;
  holding time units; `net_position(as_of)` before first fill, between fills, after last;
  realized PnL vs `closedPnl` agreement when present.

### infra/transport
- Http: 200 JSON; invalid JSON → `AdapterError`; timeout → `RetryableError`;
  5xx/429 → retryable; 400/404 → non-retryable.
- Retry: success first try (no sleep); transient then success; exhaustion raises last
  error; non-retryable not retried; backoff sequence exact.
- RateLimit: under budget no wait; over budget waits exact computed time; window reset;
  per-type weights.
- Cache: miss→hit; TTL expiry; different payload keys; errors not cached; key stable under
  dict ordering.
- Decorator stacking order produces the expected behavior (retry outside rate limit).
- Fixture: known key served; unknown key error.

### infra/gateway, adapters, pagination
- Each gateway method builds the exact payload (with/without `dex`).
- Adapters on golden fixtures; string numerics; missing optional fields; missing required
  → error; meta/ctx length mismatch; delisted flag.
- Pagination: empty; single short page; exact page-size boundary; multi-page; duplicate at
  boundary deduped; `end < start` rejected; historical cap sets `truncated`.

### session/calendar
- After close same day; before open; mid-session `is_open`; exactly 09:30 and 16:00;
  Saturday/Sunday; Monday pre-open → Friday close; holiday (e.g. Good Friday,
  Thanksgiving) skipped; early close (13:00); DST spring-forward and fall-back days;
  naive input rejected.

### universe
- No builder dexes; dex without equities; multiple equity dexes ordered by preference;
  API error on one dex doesn't hide others (recorded); each market filter pass/reject at
  exact threshold equality; empty universe after filters reported.

### wallets/sources (contract + specific)
- Contract: returns `SourceResult`; empty ok; invalid address rejected with diagnostic;
  duplicates deduped keeping newest; addresses lower-cased.
- Curated: missing file; malformed TOML; optional `raw_score`; negative score rejected.
- Paid: missing env key → disabled with diagnostic; HTTP failure → `SourceError`.
- Census: cold start empty; `min_observations` threshold equality.
- Composite: precedence on conflict; one source fails → others still returned + diagnostic;
  all fail → `SourceError`; zero sources → config error.
- Factory: each type constructs; unknown type lists valid types.

### wallets/census
- Subscribe/unsubscribe; multiple observers all notified in order; observer raising is
  isolated; non-equity fills ignored; duplicate `tid` ignored; registry round-trip;
  last_seen updates.

### wallets/filters
- Each filter: accept, reject, exact-threshold; reason text present; chain short-circuits
  at first reject; empty chain accepts; aggregate rejection counts for diagnostics.
- Maker profile: symmetric high-frequency wallet rejected; directional swing wallet kept.
- Reversal bait: synthetic history where entries precede reversals is flagged.

### wallets/scoring
- `EquitySlice` excludes crypto fills/positions entirely (wallet with only crypto → empty).
- Features: zero lots; one lot; all wins; all losses; no drawdown; hold exactly = horizon;
  prior score absent → feature skipped and weights renormalized.
- Scorer: all-zero weights rejected; trust ∈ [0,1] (property); confidence monotone in n
  (property); decay monotone in age (property); evidence contains every feature.

### signals
- Positioning: no wallets; all long (tilt 1); all short (−1); balanced (0); gross 0 → 0;
  trust weighting changes result as expected.
- Flow: no fills in window; fills just outside window excluded; boundary inclusive rule;
  OI = 0 handled; below floor → 0 with note.
- Overnight: no candles; single candle; all candles after close; ref exactly at close;
  below floor → 0; stale flag when latest candle too old.
- Corroboration: N−1 → INSUFFICIENT; exactly N → scored; low-trust wallets not counted;
  same wallet twice counted once.
- Combiner: zero score → flat; |score| = ε → flat; negative weight rejected; sign symmetry
  (property).
- Ranker: ordering; ties by ticker; INSUFFICIENT after scored.

### reporting
- Empty report; all-INSUFFICIENT report; JSON schema round-trip; Markdown/Table contain all
  evidence fields; diagnostics rendered.

### backtest
- AsOfView: access at t, beyond t raises (property with random t).
- Replay on synthetic data with a planted edge recovers positive return; with shuffled
  signals ≈ 0.
- Costs reduce returns exactly by modeled bps.
- Benchmark aligns dates, handles stock holidays/missing bars.
- Walk-forward folds non-overlapping, cover range, no test data in train.
- Metrics with zero trades; one trade; all flat.
- Insufficient-sample flag below threshold.

### app
- Config defaults; invalid values → `ConfigError` naming field; env override for secrets;
  secrets never serialized.
- Builder missing part → error; full build with fixtures.
- Pipeline end-to-end on `FixtureTransport` + `FakeClock` produces deterministic report
  (snapshot test).
- CLI exit codes for each failure class.

---

## 9. Phases

| # | Phase | Deliverables | Definition of Done |
|---|---|---|---|
| 0 | Bootstrap & API discovery | `pyproject.toml` (py3.12, ruff, mypy strict, pytest, hypothesis, import-linter), CI script, `capture-fixtures` minimal script, sanitized fixtures for `perpDexs`, `metaAndAssetCtxs(dex)`, `clearinghouseState`, `userFillsByTime`, `candleSnapshot`, `l2Book`; `docs/api-notes.md` answering every VERIFY | Fixtures committed; every VERIFY resolved in `api-notes.md`: equity dex name(s), symbol format for candles/book, fill field names and full `dir` value set, page/history caps, candle depth, rate-limit weights |
| 1 | Core & domain | `core/*`, `domain/*`, `tests/factories.py`, architecture tests, import-linter contracts | 100% branch coverage; architecture tests green |
| 2 | Infra | transport + decorators, gateway, adapters, pagination, `FixtureTransport` | Contract suite for transports green; adapters pass on golden fixtures |
| 3 | Session & universe | calendar + holiday provider, locator, market filters | DST/holiday matrix green |
| 4 | Wallet sources & census | base template, curated, composite, factory, census observer + registry; paid sources stubbed behind env | Contract suite for sources green; composite failure isolation proven |
| 5 | Wallet filters & scoring | filter chain, equity slice, features, scorer | Property tests green; `vet` command prints scores with reasons |
| 6 | Signals | features, corroboration, combiner, ranker | Sign-symmetry and bounds properties green |
| 7 | Reporting | renderers, diagnostics | Snapshot tests green |
| 8 | App & live run | config, builder, pipeline, CLI | Deterministic end-to-end test on fixtures; one manual `@live` run documented |
| 9 | Backtest | AsOfView, prices port, replay, costs, benchmark, walk-forward, metrics | Planted-edge and shuffled-signal tests behave as specified; report shows sample size and benchmark |
| 10 | Hardening | README (setup, config, interpreting output, limitations), logging, performance pass | All gates green; README explains every report field |

---

## 10. Configuration (example `config/example.toml` keys)

```toml
[universe]
known_tickers = ["AAPL","GOOGL","MSFT","META","NVDA","AMZN","TSLA"]
dex_preference = []            # filled after Phase 0
min_day_volume_usd = 1_000_000
min_open_interest_usd = 250_000

[[wallets.sources]]
type = "curated"
path = "config/wallets.toml"

[[wallets.sources]]
type = "census"
min_observations = 20

[wallets.filters]              # chain order = listed order
order = ["min_sample", "maker_profile", "inactivity", "reversal_bait"]

[wallets.scoring]
weights = { hit_rate = 1.0, drawdown = 1.0, consistency = 1.0, horizon_fit = 1.0, prior = 0.5 }
shrinkage_k = 10
half_life_days = 21
swing_horizon_days = 5

[signals]
weights = { tilt = 1.0, flow = 1.0, overnight = 0.5 }
min_flow_oi_frac = 0.02
min_overnight = 0.003
min_wallets = 3
min_trust = 0.4
epsilon = 0.01
flow_window_hours = 24

[backtest]
horizon_days = 5
cost_bps = 5
train_days = 60
test_days = 20
min_trades_for_verdict = 30
```

All numeric values above are **starting defaults to be tuned in walk-forward**, not claims of
optimality.

---

## 11. Known limitations (must appear in README)

- The HIP-3 equity segment is young: wallet history and backtest windows are short and
  dominated by one market regime. Results below `min_trades_for_verdict` are reported as
  inconclusive.
- There is no documented public leaderboard endpoint; smart-money quality depends on the
  chosen wallet sources.
- Off-hours perp prices are oracle-driven and thinly traded; overnight signals are noisiest
  when they fire.
- Visible whale positions can be deliberate bait; corroboration and the reversal-bait filter
  reduce but do not remove this.
- Output is research signal, not financial advice.

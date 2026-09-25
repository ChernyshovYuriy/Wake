# AUDIT — adversarial verification of hl-whale-signals

Date: 2026-09-24. Commit under audit: `7bf088d` (branch `main`).
Method: evidence only. Every finding cites a command's output, a `file:line`, a failing
probe test, or a surviving mutant. Probes were run in throwaway copies or reverted; no
source change from this audit remains in the tree (only this file is added).

Status legend: **PROVEN** (evidence that the invariant holds and a guard would catch a
violation), **GAP** (holds today but nothing enforces it, or the enforcement is weaker than
the spec), **BROKEN** (demonstrated false).

---

## Verdict

**Mostly as specified, with one spec-level signal defect (F4-1) and one backtest look-ahead
defect (F3-1). The tests do not enforce the plan at the boundaries.**
- Line and branch coverage is 100% in the core packages, but 62 confirmed behavior-changing
  mutants survive the full suite (Phase 6).
- The no-look-ahead property test misses a 60 s leak (F3-2).
- The guards miss most realistic violations (F2-1..F2-5).

## Fixes applied after the audit (2026-09-24)

Each fix was test-first. The new tests fail against the pre-fix code; this was checked by
restoring the old line in the scratch copy.

| Finding | Fix | Tests |
|---|---|---|
| **F4-1** corroboration counted trades of any age | `Corroboration(min_wallets, min_trust, window_hours)` counts current holders plus wallets that traded in (as_of − window, as_of]. New setting `signals.corroboration_window_hours` (default 24) | `test_rules.py`: stale trades → 0 involved; window boundaries (start excluded, as_of included); trades after as_of ignored; holders count whatever the age of their trades. `test_engine.py`: the audit probe scenario is now INSUFFICIENT. The old "in the window" test now actually places its fill in the window. 4 tests fail on the old code |
| **F3-1** backtest look-ahead through `excluded_wallets` | Exclusion is decided per view from fills ≤ t. The dict is kept only as a record for the caveat and is never consulted to skip a wallet | `test_signal_source.py::test_a_future_uninterpretable_fill_does_not_leak_into_earlier_views`, which fails on the old code at the leak assertion |
| **F2-1** pure-core guard skipped `wallets/filters.py` | The entry is `"wallets/filters.py"`; a new test requires every pure entry to match a real source file | `test_every_pure_entry_matches_a_source`. The Phase 2 probe (`import socket` + `open()` in filters.py) now fails the suite (1 failed, 24 passed) |
| **F3-2** no-look-ahead tests missed the `time == t` boundary | Deterministic, exhaustive boundary tests: every fill time and candle close at offsets −1/0/+1 ms, asserting the exact visible count; `fills_between`/`candles_between` with an event exactly at `end` and at `start`; a prior score taken exactly at `t` | `test_asof.py`. All 11 boundary/leak mutants from Phase 3 are now **killed**: `bisect_left` on fills and on candles; fills and candles leaks of +1 ms, +1 s and +60 s; end-exclusive `fills_between`/`candles_between`; `score` with `<`. Before, 9 of these survived |
| **Reversal-bait filter** (Phase 6: 15/15 logic mutants survived) | Tests only; no source change. The helper now builds long or short trips with a chosen move after each exit | `test_filters.py`: short-side bait flagged and short continuation kept; a move below `min_move` is not a reversal (long and short); a move of exactly `min_move` is one; rate exactly at the maximum accepted (3/6), 4/6 rejected with the exact reason text; unevaluable trips in the middle (held too long, no reference price) do not stop evaluation; hold exactly = window evaluated; window ending exactly at `as_of` evaluated, 1 ms later not. **15/15 mutants now killed**, both `continue → break` checked individually |

All other findings are open.

## Invariant table

Section 2 rules:

| # | Invariant | Status | Evidence |
|---|---|---|---|
| R1 | Single home per logic | **GAP** | Holds today by grep (Supplementary checks). AST guard bypassable (F2-2..F2-4). Two small re-implementations: equity restriction outside slice.py (F8-1), direction boundary re-evaluated in engine.py:50 (F8-2) |
| R2 | No copy-paste (src and tests) | PROVEN | Duplication gate catches a planted 9-line copy in src and in tests; clean tree 10.00/10 (Phase 2) |
| R3 | GoF only where named; no Singletons | PROVEN (Singletons) | No module-level instances, no `global` (Supplementary checks). The "named problem" part is a design judgment, not audited |
| R4 | Pure core: no I/O, no wall-clock | **BROKEN (guard)** | Holds today (grep: no I/O imports in pure packages). The guard skips `wallets/filters.py` entirely: planted `open()`/`socket` stays green (F2-1). `pathlib`/`os`/`tomllib` not detected (F2-5). `backtest/asof.py` not covered (F2-6) |
| R5 | Time injected; only clock.py reads "now"/sleeps | **GAP** | Holds today: every hit outside clock.py uses an injected clock or sleeper (Phase 7 grep). Guard misses `dt.now()`, `datetime.datetime.now()`, `time.time_ns()`, `t.time()`, `asyncio.sleep` (F2-2) |
| R6 | No magic numbers | PROVEN (not gated) | The only literals are unit, formula or named constants (Supplementary checks) |
| R7 | Evidence-only outputs | **GAP** | Every component and feature returns evidence, and the README test checks report fields. But mutants that delete PriorScore evidence, rename the overnight `stale` key or corrupt `n_long`/`n_short` survive (Phase 6) |
| R8 | No network in unit tests | **GAP** | Convention only. A probe test opened a TCP connection and passed; no socket blocking (Supplementary checks) |
| R9 | Typed; domain models frozen | PROVEN | `mypy --strict` clean on 163 files; only private accumulators `_Trip`/`_OpenLot` are mutable (Supplementary checks) |
| R10 | Fail loudly on unknown API shapes | PROVEN (`dir`), GAP (timing) | A bogus `dir` raises AdapterError at first use; a side contradiction raises (Phase 1). Not validated in the adapter (F1-3). Three liquidation `dir`s mapped without observation, against api-notes' own policy (F1-1) |

Section 5 Single-Home Registry:

| Row | Home | Status | Evidence |
|---|---|---|---|
| Symbol parse/format, equity test | domain/symbols.py | GAP | No second implementation (grep). Guard misses `'xyz:' + coin`, `%`, `.format`, named separator, `sep=`, slicing (F2-3) |
| `dir` → signed notional | domain/direction.py | PROVEN (logic), GAP (guard, fixtures) | Sign table verified on 4013 real fills via startPosition continuity, 0 breaks (Phase 4). Guard misses `startswith('Close')` (F2-4). 8 table entries have no fixture (F1-2) |
| FIFO lots, holding, PnL, as-of position | domain/lots.py | PROVEN (logic), GAP (tests) | FIFO == API closedPnl on 3/3 real round trips (Phase 4); API-order same-ms handling proven 100/100 vs tid 0/100. Gap-to-flat round-trip handling untested (Phase 6) |
| Now / ms⇄datetime / sleeping | core/clock.py | GAP | See R5 |
| clamp, safe_div, Wilson, shrinkage, decay | core/mathx.py | PROVEN (values), GAP (validation) | Wilson matches an independent quadratic solution to 1e-10 on 7 inputs; decay(21,21) = 0.5. Six validation mutants survive (NaN bounds, `lo == hi`, `0 < z < 1`) |
| Session calendar | session/calendar.py | PROVEN (by grep) | No session rule elsewhere; yahoo.py's NY zone is timestamp conversion only |
| Restrict fills to equities | wallets/scoring/slice.py | **GAP** | Re-implemented in app/vet.py:81 and backtest/signal_source.py:159 (F8-1) |
| Trust formula | wallets/scoring/scorer.py | PROVEN | No re-implementation (grep); property tests on bounds and monotonicity exist |
| Corroboration rule | signals/corroboration.py | **BROKEN (spec)** | Single home holds, but it counts 90-day-old trades as "trading it" and corroborates a pure market-move signal (F4-1) |
| Weighting and direction decision | signals/combiner.py | GAP | engine.py:50 re-evaluates the epsilon boundary for the reason text; its mutants survive (F8-2, Phase 6) |
| Look-ahead prevention | backtest/asof.py | **BROKEN (bypass), GAP (tests)** | The AsOfView implementation is correct, but `excluded_wallets` carries future information into earlier views (F3-1). Tests miss a 60 s leak (F3-2) |
| Defaults of every threshold | app/config.py | PROVEN | Rule 6 scan |
| Retry/backoff | infra/transport.py | PROVEN (by grep) | none elsewhere |
| `/info` payload shapes | infra/gateway.py | PROVEN (by grep) | no `"type": "<endpoint>"` literal elsewhere |

## Ranked gaps (correctness > coverage > style)

1. **F4-1 BROKEN — corroboration counts stale trades.** Three flat wallets that traded 60
   days ago "corroborate" a LONG driven only by the overnight move (probe output in
   Phase 4). The live signals are affected, and the backtest measures the same flaw. The
   test at test_rules.py:46-53 encodes it.
2. **F3-1 BROKEN — backtest look-ahead through `excluded_wallets`.** Evaluation order in
   walk-forward plus the single pass lets a future uninterpretable fill remove a wallet from
   earlier sessions (probe output in Phase 3).
3. **F3-2 GAP — no-look-ahead is not enforced at the boundary.** Leaks of up to 60 s
   (fills) and 1 s (candles) survive the full suite.
4. **Reversal-bait filter untested beyond 0% / 100% long cases.** All 15 logic mutants
   survive, including a sign flip for shorts (Phase 6).
5. **F2-1 BROKEN — the pure-core guard does not cover `wallets/filters.py`.**
6. **Boundary equalities untested across signals.** Flow floor, overnight floor and
   staleness, the reversal threshold, the weak-confidence flag, ranker with a zero-score
   scored signal, `TickerSignal` score = ±1, and the overnight lower clamp (a −2 component
   survives) (Phase 6).
7. **B-1 GAP — per-package 100% coverage is not gated.** Only 95% overall.
8. **F2-2..F2-5 GAP — guards detect only the spelling in their planted example.**
9. **Rule 8 convention only**: unit tests can reach the network.
10. **Consistency, horizon-fit and drawdown feature gaps**: period bucketing, zero-hold
    wallet → full horizon fit, undefined returns (Phase 6).
11. **F1-1/F1-2/F1-4 — `dir` provenance**: three inferred liquidation dirs, eight mapped
    values without a fixture, api-notes self-contradiction.
12. **F8-1, F8-2 — minor single-home duplications**; F7-1 curated prior score silently
    unusable in backtests; B-3; the Phase 5 minors.
13. **Plan §8 is wrong on same-timestamp ordering** ("by `tid`"). The code is right (100/100
    vs 0/100 on real fills); the plan should be amended, not the code.

## Proposed missing tests (derived from the spec, not from current behavior)

Each test cites the spec text it enforces. The tests marked ✗ would **fail today**.

- **T-1 ✗ (§6.10 "recent fills"; corroboration "hold/trade it").** Three trusted wallets,
  flat, whose only fills on the symbol are older than the flow window (or than a dedicated
  corroboration window) → `INSUFFICIENT`. Also, the direction must never come from
  `overnight` alone when `tilt` and `flow` are both zero, unless the spec is amended to
  allow it.
- **T-2 ✗ (§6.12 "AsOfView ... every accessor filters `time <= t`").** Property test with `t`
  drawn from `{event_time + d : d ∈ {-1, 0, +1}}` for every fill and candle, asserting the
  row is visible iff `time <= t`. The same for `fills_between`/`candles_between` with
  `end == t`, and for `score` with `as_of == t`. This kills every Phase 3 leak mutant.
- **T-3 ✗ (§6.12 look-ahead, §7 "AsOfView never leaks").** Order independence: for a
  dataset containing an uninterpretable fill at `t2`, `inputs_at(view(t1))` for `t1 < t2`
  is identical whether evaluated before or after `view(t2)`, including through
  `with_engine`.
- **T-4 (§8 lots "realized PnL vs closedPnl agreement").** On the committed
  `fills_heavy_page1/2` fixtures: for every non-orphan round trip, FIFO PnL equals the sum
  of the API `closedPnl` of the fills that closed it (3/3 today). Also: `startPosition`
  continuity over all fixture fills (0 breaks today).
- **T-5 (§8 wallets/filters "each filter: accept, reject, exact-threshold").** Reversal
  bait:
  - a mirrored **short** bait history is rejected;
  - a 3-of-6 history is accepted at `max_reversal_rate = 0.5` and a 4-of-6 one rejected;
  - the reason reports exactly `"4/6"`;
  - a non-evaluable trip in the middle does not stop evaluation of later trips;
  - `min_move` equality counts as a reversal.
- **T-6 (§8 signals "below floor → 0", "|score| = ε → flat").**
  - `|flow/OI| == min_oi_frac` and `|log move| == min_move` pass through (not zeroed).
  - Staleness at exactly `max_staleness_hours` is not stale.
  - An overnight move of −6% gives a component of exactly −1.
  - A flat signal's reason says `<=`.
  - `TickerSignal(score=±1.0)` constructs.
- **T-7 (§6.10 Ranker "INSUFFICIENT listed after scored").** A scored FLAT signal with
  score 0.0 on ticker `ZZZ` ranks before an INSUFFICIENT signal on `AAA`.
- **T-8 (§6.9 features).**
  - Consistency: two trips in the same period with PnL +1 and −2 give one negative
    period.
  - A zero-PnL period is not positive.
  - A wallet with median hold 0 gets horizon fit 0.
  - PriorScore evidence contains `raw_score` and `scale` (rule 7).
- **T-9 (§6.2 lots, orphan and gap handling).**
  - After a gap to flat followed by a new trip, `round_trips` holds exactly the new trip
    with the correct side and open time.
  - `PositionGap.time_ms` and `tid` equal the offending fill's.
- **T-10 (§5 enforcement).** The architecture guard detects:
  - every Phase 2 variant;
  - every entry of `PURE_PACKAGES` matches at least one file (this would have caught F2-1);
  - `backtest/asof.py` is in the pure set.
- **T-11 (§7 coverage gates).** CI fails if any of core, domain, signals, wallets/scoring,
  wallets/filters, session or backtest/asof drops below 100% branch coverage (for example
  `coverage report --include=... --fail-under=100` per package).
- **T-12 (rule 8).** A root conftest blocks sockets (for example `pytest-socket` with
  `--disable-socket`, and `live` tests re-enabling it). A test that opens a socket must fail.
- **T-13 (§6.2 / rule 10).** `dir_catalog.json` holds one real captured fill per mapped
  value; `adapt_fills` rejects an unknown `dir` at parse time.
- **T-14 (§6.1 mathx).** `clamp` rejects NaN in `lo` and in `hi`; `clamp(x, a, a) == a`;
  `safe_div` rejects NaN in `b` and in `default`; `wilson_lower_bound(…, z=0.5)` is valid.

---

## Phase 0 — Baseline

Commands run from the repo root with `.venv/bin/`:

| Gate | Command | Result |
|---|---|---|
| ruff | `ruff check src tests scripts` | All checks passed |
| format | `ruff format --check src tests scripts` | 164 files already formatted |
| mypy | `mypy` (`strict = true`, `files = ["src","tests","scripts"]`, pyproject.toml:62-65) | Success: no issues found in 163 source files |
| import-linter | `lint-imports` | 3 contracts kept, 0 broken |
| duplication | `pylint --disable=all --enable=duplicate-code --min-similarity-lines=6 --ignore=fixtures src tests` | 10.00/10 |
| pytest | `pytest --cov --cov-branch` | 1046 passed, 4 deselected (live), total coverage 99% |

Per-package branch coverage (from `coverage json`), against plan §7's 100% targets:

| Package (plan §7 target) | Statements | Branches |
|---|---|---|
| core (100%) | 163/163 | 42/42 |
| domain (100%) | 490/490 | 62/62 |
| signals (100%) | 214/214 | 56/56 |
| wallets/scoring (100%) | 187/187 | 36/36 |
| wallets/filters (100%) | 96/96 | 30/30 |
| session (100%) | 109/109 | 34/34 |
| backtest/asof (100%) | 57/57 | 8/8 |
| app | 1330/1342 | 189/194 (cli.py serve/dashboard paths, dashboard_data 109-110, system_status 43-44, backtest.py branch 176->178) |
| backtest/signal_source | 92/93 | 11/12 (line 157) |
| infra | 520/523 | 110/110 (recording.py:58, tape_feed.py:54-55) |
| all others | 100% | 100% |

**Baseline findings**

- **B-1 GAP — the 100% per-package coverage gate is not wired.** Plan §7 requires 100%
  branch coverage for core, domain, signals, wallets/scoring, wallets/filters, session and
  backtest/asof. `scripts/ci.sh:17` enforces only `--cov-fail-under=95` overall, and no
  other file sets a per-package threshold (`grep -rn "fail-under\|fail_under"` finds only
  ci.sh:17). Those packages happen to be at 100% today, but a change that dropped
  `signals/` to 80% would stay green as long as the total stays ≥ 95%.
- **B-2 OK — exclusions are few and named.** `pragma: no cover` appears twice
  (`app/cli.py:455` `__main__` guard; `app/config.py:310` unreachable type-dispatch
  fallback). Suppressions: one `noqa` in `domain/symbols.py:37`, one in `app/cli.py:98`, one
  `type: ignore` in `app/config.py:284`. No path is omitted in `[tool.coverage]`
  (pyproject: `source = ["hlsignals"]`, no `omit`).
- **B-3 GAP (minor) — coverage is measured on `src` only.** `source = ["hlsignals"]`, so
  dead test helpers go unnoticed. This does not affect the product.

---

## Phase 1 — Fixture reality check (Phase 0 VERIFYs)

18 fixtures are committed (`git ls-files tests/fixtures | wc -l` → 18).

| VERIFY (plan) | api-notes answer | Fixture evidence | Status |
|---|---|---|---|
| Fill field names (`crossed`, `closedPnl`, `tid`, `hash`) §6.2 | §3 | `hl/dir_catalog.json`, `hl/fills_*.json` contain `crossed` (bool), `closedPnl`, `tid`, `hash`, `startPosition` | PROVEN |
| Full `dir` value set §6.2 | §4: 6 perp + 4 non-perp | Only the 6 perp values occur in any fixture (script below) | **GAP**, see F1-1, F1-2 |
| Page cap ~2000 §6.3 | §5: 2000 | `fills_heavy_page1.json` and `page2` have exactly 2000 rows each, ascending `time` | PROVEN |
| `startTime` inclusive / boundary duplicate | §5 | page2 `startTime` = page1's last `time` (1790197003761); the two pages share exactly **1** tid | PROVEN |
| History cap ~10,000 fills §6.3 | §5 line 145 | No fixture shows it (largest capture: 2 × 2000 fills) | GAP: empirical claim, not committed |
| Candle depth cap §6.12 | §7: ~5000 per interval | 1h fixture: 73 candles, 1d: 31. Neither reaches a cap | GAP: empirical claim, not committed |
| Rate-limit weights §6.3 | §8: 1200/min/IP | No fixture (a limit cannot be captured as a response); the 429 is not captured either | GAP: from docs plus an uncommitted experiment |
| WS channel availability §6.7 | §9: `trades` with `users`, no `allFills` | `recent_trades_xyz_nvda.json` rows have `users` | PROVEN for the REST shape; WS itself not captured |
| Equity dex names, symbol format | §1-2 | `perp_dexs.json` lists `xyz, flx, vntl, hyna, km, abcd, cash, para, mkts, io`; `error_candles_bare_coin.json` (bare `NVDA` → 500) shows the dex prefix is required | PROVEN |
| `clearinghouseState` needs `dex` | §2 | `clearinghouse_positioned_xyz.json` request carries `"dex": "xyz"` | PROVEN |

Distinct `dir` values across **all** fixtures (Python walk over every JSON object with a
string `dir`):

```
dir_catalog.json      {Close Long, Close Short, Long > Short, Open Long, Open Short, Short > Long}
fills_active.json     {Open Short: 9, Close Short: 5}
fills_heavy_page1     {Open Long: 638, Close Short: 241, Open Short: 363, Close Long: 753, Long > Short: 3, Short > Long: 2}
fills_heavy_page2     {Open Short: 528, Close Short: 534, Close Long: 396, Open Long: 539, Short > Long: 3}
ALL = [Close Long, Close Short, Long > Short, Open Long, Open Short, Short > Long]
```

Diff against `domain/direction.py:18-38`:

- (a) Table entries with **no fixture backing** (8):
  - `NON_PERP_DIRS` `Buy`, `Sell`, `Merge Outcome`, `Spot Dust Conversion`. api-notes §4 says
    they were "observed across ~62k fills", but the raw data was not committed.
    `dir_catalog.json` claims "one real fill per observed dir value" yet holds only the six
    perp values.
  - `Liquidated Isolated Long`: observed in the 2026-09-24 live backtest (docs/api-notes.md §4
    update), but no fixture was captured.
  - `Liquidated Isolated Short`, `Liquidated Cross Long`, `Liquidated Cross Short`: **never
    observed**. `direction.py:27-29` says so itself ("the other three follow the same
    structure and are unobserved").
- (b) Fixture values with no table entry: **none**.

Rule 10 probe (`scratchpad/audit/probe_dir.py`): `fills_active.json` with one fill's `dir`
set to `"Bogus Direction"`:

```
adapter accepted bogus dir: Bogus Direction
LotBook raised AdapterError: unknown fill dir 'Bogus Direction'
signed_notional raised AdapterError: unknown fill dir 'Bogus Direction'
side mismatch raised: fill side <Side.BUY: 'B'> contradicts dir 'Open Short' (tid 403279425470566)
```

`grep -rn "\.dir\b" src` shows that only `direction.py` reads `dir`, and every consumer
(`lots.py:142`, `signals/features.py:76`) goes through `signed_size`/`signed_notional`.
Unknown values raise. **Rule 10 PROVEN for `dir`**, with one caveat (F1-3).

**Findings**

- **F1-1 GAP (spec process) — three `dir` mappings are inferred, not observed.**
  api-notes §4 states the policy "Values documented elsewhere but not observed ... will raise
  `AdapterError` (fail loud, plan rule 10) and get added when seen". The three unobserved
  `Liquidated ...` entries break that policy. Their sign is still protected: `signed_size`
  rejects any fill whose side contradicts the mapped sign (`direction.py:57-59`, probe
  above). So a wrong guess raises rather than mis-signs. What remains unprotected is the
  *semantic* assumption that these are ordinary exposure-reducing trades.
- **F1-2 GAP — the four non-perp values and the one observed liquidation value have no
  fixture.** Evidence for them lives only in prose. `dir_catalog.json` should contain one
  real fill per mapped value.
- **F1-3 GAP — `dir` is validated late, not at the adapter.** `adapters.py:192` stores
  any string, so a bogus `dir` passes `adapt_fills` (probe line 1). It fails at first
  interpretation. Today every consumer interprets it, so the failure is loud. A future
  consumer that only counts fills (such as the maker prescreen, `app/vet.py`) would silently
  accept garbage.
- **F1-4 BROKEN (docs) — api-notes contradicts itself on liquidations.** §3
  (docs/api-notes.md:100-101): "`dir` stays the ordinary value ... liquidation is **not**
  encoded in `dir`". The §4 update (line 129) records `Liquidated Isolated Long` observed
  live. §10 (line 232) still lists "Liquidation ... `dir` variants: not observed". Two of the
  three statements are stale.

---

## Phase 2 — Are the guards real?

Method: each violation was planted in a module of a **throwaway copy** of the repo
(`scratchpad/audit/copy`), and the real guard was run against it: `pytest tests/architecture`,
`lint-imports` (with `PYTHONPATH` pointing at the copy), and the pylint duplication command
from `ci.sh`. The working tree was never touched (`git status` is clean apart from this file).

### Single-home AST guard (`tests/architecture/test_single_home.py`)

The suite plants one violation per rule (`PLANTED`, lines 147-158), but only the exact
spelling the author chose. Realistic variants planted into `src/hlsignals/signals/_probe_N.py`:

| Rule | Planted code | Guard |
|---|---|---|
| clock (rule 5) | `from datetime import datetime; datetime.now()` (spec example) | red, detected |
| clock | `from datetime import datetime as dt; dt.now()` | **GREEN, not detected** |
| clock | `import datetime; datetime.datetime.now()` | **GREEN** |
| clock | `import time; time.time_ns()` | **GREEN** |
| clock | `import time as t; t.time()` | **GREEN** |
| clock | `await asyncio.sleep(1)` | **GREEN** |
| symbols (§5 row 1) | `dex + ':' + coin` (spec example) | red, detected |
| symbols | `'xyz:' + coin` | **GREEN** |
| symbols | `'%s:%s' % (dex, coin)` | **GREEN** |
| symbols | `'{}:{}'.format(dex, coin)` | **GREEN** |
| symbols | `SEP = ':'; dex + SEP + coin` | **GREEN** |
| symbols | `s.split(sep=':')` | **GREEN** |
| symbols | `s[: s.index(':')]` | **GREEN** |
| dir (§5 row 2) | `d == 'Open Long'` (spec example) | red, detected |
| dir | `d.startswith('Close')` | **GREEN** |
| dir | `d.endswith('Long')` | **GREEN** |
| dir | `{'Open ' + 'Long': 1, ...}` (a dir map outside direction.py) | **GREEN** |
| pure I/O (rule 4) | `open('x').read()` in `signals/` | red, detected |
| pure I/O | `Path('x').read_text()` in `signals/` | **GREEN** |
| pure I/O | `os.listdir('.')` in `signals/` | **GREEN** |
| pure I/O | `tomllib.load(fh)` in `signals/` | **GREEN** |
| pure I/O | `import socket` + `open('x')` appended to **`wallets/filters.py`** | **GREEN (19 passed)**; the same `open()` in `signals/combiner.py` → 1 failed |

None of the undetected patterns exists in `src` today: `grep -rnE` for `'<word>:' +`,
`%s:%s`, `{}:{}`, `startswith("Open|Close`, `endswith("Long|Short`, `time_ns`,
`datetime.datetime.now` and `Path(...).read_/write_` returned nothing, and no pure package
imports `pathlib/os/tomllib/asyncio`. So the rules **hold today**, but the guards would not
catch a regression.

- **F2-1 BROKEN — rule 4 (pure core, no I/O) is not enforced for `wallets/filters`.**
  `PURE_PACKAGES` (test_single_home.py:19) lists `"wallets/filters/"`, but filters is a
  module, `wallets/filters.py`. `p.startswith("wallets/filters/")` is never true for it, so
  the I/O rule silently skips the file. Demonstrated: `import socket` plus `open("x")`
  appended to `wallets/filters.py` leaves the architecture suite green (19 passed).
- **F2-2 GAP — the clock guard matches only `<datetime|date|time>.<attr>` on a bare name.**
  Aliases (`dt.now`, `t.time`), qualified access (`datetime.datetime.now`), `time_ns`
  and `asyncio.sleep` all pass (`reads_time`, test_single_home.py:31-44).
- **F2-3 GAP — the symbol guard matches only a standalone `":"` constant.** A prefix
  literal (`'xyz:' + coin`), `%`-formatting, `.format`, a named separator, keyword `sep=`
  and slicing on `index(':')` all pass (`builds_symbol_strings`, lines 47-69).
- **F2-4 GAP — the dir guard matches only whole literals.** Prefix and suffix
  interpretation (`startswith('Close')`) is the most natural way to re-implement direction
  logic, and it passes (`interprets_dir`, lines 76-79).
- **F2-5 GAP — the I/O guard knows only `open()` and a module deny-list.** `pathlib`,
  `os` and `tomllib` are missing (`IO_MODULES`, line 20).
- **F2-6 GAP — `backtest/asof.py`** is one of the pure 100%-coverage modules in plan §7, but
  it is not in `PURE_PACKAGES`, so rule 4 is not checked there.

### import-linter (`.importlinter`)

| Planted import | Result |
|---|---|
| `signals/combiner.py`: `import hlsignals.infra.transport` (sibling) | 2 broken, detected |
| `domain/lots.py`: `import hlsignals.signals.combiner` (upward) | 1 broken, detected |
| `backtest/replay.py`: `import hlsignals.infra.gateway` (forbidden) | 1 broken, detected |
| `session/calendar.py`: `import hlsignals.domain.models` (forbidden) | 1 broken, detected |
| `signals/combiner.py`: `from hlsignals import app` | 1 broken, detected |
| `signals/combiner.py`: import inside a function body | 2 broken, detected |
| `core/mathx.py`: `importlib.import_module('hlsignals.infra.transport')` | kept, not detected (expected: static analysis) |

**import-linter: PROVEN** for static imports.

### Duplication gate

Planting the same 9-line function in `signals/combiner.py` and `wallets/filters.py` gives 1
R0801. Planting it in two test files gives 1 R0801. Clean tree: 0. **PROVEN** for blocks of 6
or more lines. (It cannot detect semantic re-implementation, which is what the AST guard is
for; see F2-2..F2-4.)

---

## Phase 3 — No look-ahead

### Is AsOfView the only path to historical data in `backtest/`?

`grep -n "data\.\|\.fills\|\.candles\|\.records\|\.markets" src/hlsignals/backtest/*.py`
(excluding asof.py) shows these reads:

- `replay.py:121` `self._data.at(...)`: views are only built through `HistoricalData.at`.
- `signal_source.py` reads only through `view.fills_between` (158), `view.candles` /
  `candles_between` (61, 144, 179), `view.score` (168), `view.records` (155) and
  `view.markets` (130).
- Outcomes (stock returns) come from `PriceBook` in `replay.py`, by design, not a view.

The fill and candle paths all go through the time filter. Two accessors are **not**
time-filtered, by documented design:

- `view.records`: today's wallet list (selection bias, `STANDING_CAVEATS[1]`).
- `view.markets`: today's snapshot, used for `open_interest` (signal_source.py:191,
  `STANDING_CAVEATS[0]`) and for universe membership (`STANDING_CAVEATS[2]`).

These are disclosed limitations, not bugs. Nothing else in `backtest/` touches
`HistoricalData` internals (`_data` is used only inside asof.py).

### F3-1 BROKEN — look-ahead through `HistoricalSignalSource.excluded_wallets`

`signal_source.py:102` keeps one mutable `excluded_wallets` dict per source. `with_engine`
(line 116) shares it with every walk-forward grid variant. `_accepted` skips any wallet in
it (156-157) and adds a wallet when *any* fill in its lookback up to t cannot be
interpreted (170-171). The set therefore carries information from the latest `t` evaluated
so far into every view built afterwards, including views at **earlier** `t`.

Probe `scratchpad/audit/probe_exclusion_lookahead.py` (synthetic scenario from
`tests/backtest/test_signal_source.py`, one `dir="Mystery Direction"` fill placed 5 days
*after* `AS_OF`):

```
fresh source, view at AS_OF only        : True     <- wallet visible, correct
excluded after later view               : True
same source, view at AS_OF built after  : False    <- wallet removed by a future fill
with_engine copy, view at AS_OF         : False
```

This path is reached in production. `run_backtest` evaluates views out of chronological
order: `WalkForward.run` (walkforward.py:77-81) replays fold k's train then its test, then
fold k+1's train, which starts earlier. `app/backtest.py:227-228` then runs the full
single pass over all days. With the defaults (train 60, test 20, embargo 4):

1. Fold 1 builds days `[0,56)` and then `[60,80)`.
2. Days 56-59 are built for the first time only later, by fold 2's train or the single pass.

Any wallet excluded because of a fill in days 60-79 is therefore missing from days 56-59.
The per-`t` inputs cache (line 123-126) hides the effect for days already built, so the
result depends on evaluation order. Severity: needs an uninterpretable fill (today only an
unknown `dir` or a side contradiction), but when it happens, results are silently wrong in a
look-ahead direction.

### F3-2 GAP — the no-look-ahead property test does not reach the boundary

`tests/backtest/test_asof.py:36-42` draws `t` uniformly over a ~202 h range in
milliseconds. Fills sit on 6 h boundaries, so `t == fill.time_ms` is effectively never drawn
and the `time <= t` edge is never exercised. Mutants of `asof.py` (all in the throwaway
copy, full suite, 3 runs each where noted):

| Mutant | Result |
|---|---|
| `fills()` `bisect_right` → `bisect_left` (drops rows at exactly t) | **SURVIVED 3/3** |
| `candles()` `bisect_right` → `bisect_left` | **SURVIVED** |
| `fills()` shows rows up to **t + 1 ms** (a real look-ahead) | **SURVIVED 3/3** |
| `fills()` up to t + 1 s | **SURVIVED 3/3** |
| `fills()` up to t + 60 s | **SURVIVED 3/3** |
| `fills()` up to t + 10 min | killed 3/3 |
| `candles()` up to t + 1 ms | **SURVIVED 3/3** |
| `candles()` up to t + 1 s | **SURVIVED 3/3** |
| `candles()` up to t + 60 s | killed 3/3 |
| `_check`: `end_ms > t` → `>=` | killed |
| `_check`: `end_ms > t` → `> t + 1` | killed |
| `fills_between`: `start < time` → `<=` | killed |
| `fills_between`: `time <= end` → `<` | **SURVIVED** |
| `candles_between`: `start <= open` → `<` | killed |
| `score`: `as_of <= t` → `<` | **SURVIVED** |

(A first run reported the two `bisect_left` mutants as killed. That was spurious: the
mutant referenced an unimported `bisect_left`, and the NameError was the "kill". I reran
them with the import added; they survive.)

**Verdict for §6.12 "every accessor filters `time <= t`"**:
- The *implementation* is correct: I read `asof.py:57-80` and the probes show no leak in
  the real code.
- The *tests do not enforce it*. A look-ahead of up to 60 s on fills, or 1 s on candles, is
  undetectable.
- The `LookAheadError` path is enforced (`_check` mutants killed).

---

## Phase 4 — Independent recomputation

### direction.signed_notional: PROVEN on real data

Independent check that uses no test code: in the captured fills, each fill's
`startPosition + signed_size` must equal the next fill's `startPosition` on the same coin.
This validates the sign of every mapped perp `dir` against the exchange's own position
accounting.

```
heavy p1+p2   fills 3999  continuity ok 3942  breaks 0  flips crossing zero 8
active        fills   14  continuity ok   13  breaks 0
```

All 8 real flips (`Long > Short`, `Short > Long`) cross zero. The unit tests assert exact
values, not just the absence of errors: `test_signed_size_and_notional` expects −3.0 for
Open Short sz 2 @ 1.5, and the property test checks mirroring and the px·sz magnitude.

### lots.LotBook

| Case | Hand derivation | Test asserts | Status |
|---|---|---|---|
| Flip through zero: OL 2@100, L>S 5@90, CS 3@80 | long 2: (90−100)·2 = −20; short 3 opened @90: (90−80)·3 = +30; flat | −20, +30, entry 90, position 0 (test_lots.py:63-71) | PROVEN |
| Orphan close: start 5, CL 3@110 | lot size 3, entry unknown, PnL None; 2 left | exactly that (74-83) | PROVEN |
| closedPnl vs FIFO, synthetic: OL 1@100, OL 1@120, CL 1@130, CL 1@140 | FIFO 30+20 = 50; average-cost 20+30 = 50 | 50 == 50 (174-190) | PROVEN |
| **closedPnl vs FIFO, real fixture** | LotBook on 3999 real fills: 16 round trips, 3 non-orphan. Per trip, FIFO PnL vs Σ API `closedPnl` of the trip's fills (a flip's closedPnl belongs to the trip it closes) | none: no test runs this on real fills | **Agreement exact on 3/3** (e.g. META short 0.267: (750.5−747.07)·0.267 = 0.91581 = API). Not enforced by a test: GAP (T-4) |
| `net_position` boundaries | before first fill → 0 (flat start) or None (truncated); at fill time inclusive; between; after | test_lots.py:147-160 assert each | PROVEN |

**Same-timestamp ordering: the plan is wrong and the code is right.**
- Plan §8 says "same-timestamp ordering by `tid`". The code keeps API order instead
  (lots.py:3-4), and `test_same_timestamp_keeps_feed_order_not_tid_order` pins that.
- Fixture evidence: the real pages contain 179 same-(ms, coin) groups, 100 of them with
  non-monotonic `tid`. API order chains `startPosition` in **100/100** of those groups;
  `tid` order chains in **0/100**. The deviation is **PROVEN correct**; plan §8 should be
  amended.

### mathx

- `wilson_lower_bound` against an independent formula (the lower root of the score-interval
  quadratic): identical to 10 decimal places at (8,10), (10,10), (0,10), (1,1), (15,30),
  (29,30), (3,1000). The test asserts (8,10) → 0.4902. By hand:
  (0.99208 − 0.313624)/1.38416 = 0.49016. PROVEN.
- `half_life_decay(21, 21)` = 0.5 exactly (0.5**1.0); the test asserts `approx(0.5)`.
  PROVEN.

### Signal boundaries

- **Flow window**: `start < time <= as_of` (features.py:73), i.e. (as_of − window, as_of].
  Boundary mutants are in Phase 6.
- **Flow and overnight floors**: `abs(x) < min` → 0 (features.py:88, 128). At exact
  equality the component passes, consistent with "zeroed *below*" (plan §6.10). Mutants are
  in Phase 6.
- **Combiner**: `abs(score) <= epsilon` → flat (combiner.py:37), as the spec requires. The
  score is normalized by Σw, a documented deviation from "score = Σ weight·feature"
  (api-notes §16, line 323). Mutants are in Phase 6.
- **Corroboration N vs N−1**: tests assert exactly N → sufficient and N−1 → insufficient
  (test_rules.py:34-53). The `>= min_trust` boundary is pinned at 0.39 vs 0.4 (56-70).

### F4-1 BROKEN — corroboration and signals count trades of any age in the 90-day lookback

- Plan §6.10: Inputs are "trusted wallets' current equity positions + **recent** fills", and
  a ticker is `INSUFFICIENT` "unless enough distinct trusted wallets hold/trade it".
- Implementation: `TickerInputs.fills` is the wallet's whole scoring history on the symbol,
  the full 90-day lookback (`app/pipeline.py:196-198`; the same in
  `backtest/signal_source.py:141-143`). `Corroboration.check` counts every wallet with any
  fill (`corroboration.py:35`), with no window.

Probe `scratchpad/audit/probe_stale_corroboration.py`, using default `SignalSettings`, the
real engine and the factories. Three wallets with trust 0.9 each closed an NVDA trade **60
days ago** and are flat now; there are no positions and no flow; the perp is +3% since the
last close:

```
status: scored | direction: long | score: 0.1971
  tilt      value=+0.000  (net_usd 0, gross_usd 0, n_long 0)
  flow      value=+0.000  (flow_usd 0, n_fills 0)
  overnight value=+0.985  (ref 100 → last 103)
  reason: long: |score +0.197| > epsilon 0.05; 3 trusted wallets >= 3 (3 involved, trust >= 0.4)
```

The report presents a pure market-move signal as smart-money-corroborated. That defeats
the purpose of corroboration (§6.10) and would pass through the backtest the same way.

**The test suite encodes the bug.** `test_rules.py:46-53` says in a comment "C traded **in
the window** without holding now", but its fill has the factory's default time, and the code
has no window to be in. Any fill age passes. A window-respecting test (T-1) would fail today.

---

## Phase 5 — Coverage is not assertion

Method: an AST scan of every `test_*` function in `tests/` for (a) no `assert` and no
`pytest.raises`/`warns`, and not calling a helper that asserts; and (b) assertions that are
all weak: bare truthiness, `not x`, `is not None`, `> 0`, `isinstance`.

- (a) **1 test** has no assertion: `tests/core/test_clock.py:108
  test_system_sleeper_zero_returns`. It only checks that the call returns, which is its
  intent for a zero sleep. Benign.
- (b) **27 tests** assert only booleans. Reading each one:
  - **Fine (22):** the boolean *is* the spec. Examples: `Verdict.accepted` in the filter
    tests, `is_open` at DST boundaries, `truncated` flags, `verdict().startswith(...)` with
    the numbers inside the string.
  - `tests/infra/test_gateway.py:48 test_clearinghouse_state_with_dex` is truthiness only,
    but the same fixture's content is asserted field by field in
    `tests/infra/test_adapters.py:115-139`. Covered.
  - `tests/wallets/test_census.py:67 test_recorder_upserts_both_counterparties` checks only
    `is not None` for both counterparties. `n_fills` is asserted in lines 87 and 98, but
    **`first_seen`/`last_seen` after one upsert are not asserted there**. Minor GAP.
  - `tests/app/test_vet.py:230 test_prescreen_skipped_for_partial_first_page`: `scored is
    not None` shows the wallet went past the prescreen, but not that it was scored
    correctly. Minor.
- **Snapshot tests** (`tests/reporting/test_renderers.py:39-44`,
  `tests/app/test_pipeline.py:68-73`): a missing snapshot raises `FileNotFoundError`, so they
  cannot pass vacuously. `UPDATE_SNAPSHOTS=1` accepts any output by design.
  `test_pipeline.py` adds semantic assertions (statuses, directions, wallet counts, lines
  76-85), so the snapshot is not the only check. PROVEN.
- **Sign-symmetry property** (plan §7): `tests/signals/test_engine.py:144-197` mirrors
  fills, positions and candles, and asserts every component and the score flip to within
  1e-9. PROVEN.

**Tests that faithfully encode a bug (the main target of this audit):**

- `tests/signals/test_rules.py:46-53`: see F4-1. It is named and commented as "traded
  **in the window**", but it would pass for a fill of any age.
- `tests/backtest/test_asof.py:36-42`: see F3-2. The "never leaks" property test samples
  `t` so that `time == t` and `t + ε` are never exercised; a 60 s fill leak survives.
- `tests/app/test_cli.py:287-310` asserted, until commit `7bf088d` (today), that an
  empty walk-forward reported `"out-of-sample walk-forward"`. It was fixed before this
  audit, but it is a precedent for the pattern.

---

## Phase 6 — Mutation testing

Tool: **mutmut 3.8.0**, installed in a scratch virtualenv (`scratchpad/audit/mvenv`) and
run on the throwaway copy. Neither the project `.venv` nor the repo was touched. Scope, as
the brief asked: `domain/*`, `signals/*`, `wallets/scoring/*`, `wallets/filters.py`,
`backtest/asof.py`, `core/mathx.py`. `tests/architecture` was excluded from mutant runs,
because its source scan would flag mutmut's own trampolines.

```
1716 mutants   🎉 killed 1485   ⏰ timeout 5   🙁 survived 226   (3.11 mutations/s)
```

**mutmut's survivor list is not reliable on its own.** mutmut picks which tests to run per
mutant. I therefore re-ran every survivor that changes logic (102) against the **full
suite** with a 180 s timeout (`scratchpad/audit/verify_all.py`; the source was restored
after each mutant, and `diff -rq` against the original is clean afterwards):

- **5 were false survivors**, killed by the full suite: `lots._match`
  `if lot.size == 0` → `!= 0` / `== 1`, and three `while` condition mutants.
- **97 survive the full suite.**

The remaining 129 survivors (93 in validators, 28 in message strings, 8 in error
construction) were classified from their diffs but not re-run individually.

Adjusted score for the audited scope: 1495 killed or timed out out of 1716 = **87.1%**.

### Classification of the 97 confirmed logic survivors

**Equivalent (28): no observable change is possible, given invariants proven elsewhere.**
- `sign >= 0` in `direction.signed_size` (×2): `sign` is never 0.
- `_side_of` `>= 0`, and four `_match` comparisons (`lots[0].size >= 0`, `remaining >= 0`,
  `lot.size <= 0`, `remaining <= 0`): sizes are never 0 inside the loop.
- `clamp` bounds widened to ±2 in `combiner`, `tilt` and the `scorer` (×6): the inputs are
  already in range.
- `LONG if score >= 0`: score 0 is already flat.
- `Symbol.parse` (×2): the redundant check is re-enforced by `Symbol.__post_init__`.
- `Corroboration.check`: `False` → `None` is falsy.
- `_Trip(orphan=...)` variants (×3): closing an orphan lot sets `orphan` anyway.
- `HorizonFit` (×5): ratio > 0 branches.
- `Flow` `oi <= 1`.
- Scorer `continue` → `break` (equivalent only because `prior_score` is the last feature).
- Ranker `else 2`.

**Cosmetic (7):** message text in `MakerProfileFilter` (×4), and the flow, overnight and
engine notes.

**Real test gaps (62):** each one is a behavior change that no test detects.

| Area | Surviving mutants (file) | What is untested |
|---|---|---|
| **Reversal-bait filter (15/15 logic mutants survive)** | `wallets/filters.py:120-133`: `direction` for shorts `-1` → `+1` / `-2`; long `1` → `2`; `(after-ref)/ref` → `*ref`; `<= -min_move` → `<=` / `< +min_move`; `reversals += 2`; `rate = reversals * evaluable`; `rate <= max` → `<`; `continue` → `break` (×2); `ref is None or after is None` → `and`; window `held > window` → `>=`; `after_ms > as_of` → `>=` | **The filter's arithmetic is effectively unconstrained.** Tests use only a 0% or a 100% reversal rate on **long** trips. The short side, the rate value, the threshold equality and the skipping of non-evaluable trips are all untested. Plan §8 asks for "exact-threshold" tests for every filter. |
| Consistency feature (6) | `wallets/scoring/features.py`: `close_ms // period` → `/` (a bucket per trip instead of per period); `get(bucket, 0.0)` → `1.0` / `get(None, …)`; `realized_pnl or 0` → `or 1`; `pnl > 0` → `>= 0` / `> 1` | There is no test with two trips in the same period, with a zero-PnL period, or with an orphan trip. "Share of positive periods" is not pinned. |
| Overnight feature (7) | `signals/features.py:113-130`: stale flag in the no-reference branch `True` → `False`; evidence key `"stale"` renamed (×2); `latest_close <= as_of` → `<`; staleness `>` → `>=`; floor `<` → `<=`; clamp lower bound `-1` → `-2` | **A move below −3% (full-scale) is never tested**, so a component of −2 would pass. The floor and staleness equality are untested. The stale flag in the missing-reference branch is untested. |
| Flow floor (1) | `features.py:88` `<` → `<=` | `|flow/OI| == min_oi_frac` exactly |
| Positioning evidence (3) | `n_long += size >= 0`, `n_short += size <= 0` / `< 1` | zero-size positions in the `n_long`/`n_short` evidence (rule 7) |
| Engine reason and flags (3) | `engine.py:50` `or True` / `>=`; `_flags` `<` → `<=` | The reason text for a **flat** signal (it would say `>` epsilon) and the weak-confidence equality. See F8-2. |
| Ranker (5) | `ranker.py`: `scored = None`; `0 if scored` → `1` / `and False` / `or True`; `score or 0.0` → `or 1.0` | "INSUFFICIENT after scored" (plan §6.10) holds only because INSUFFICIENT has score `None` → 0. **No test puts a scored signal with score 0 against an INSUFFICIENT one with an earlier ticker.** |
| Prior-score evidence (5) | `features.py` PriorScore: evidence `{}` or keys renamed | Its evidence content is never asserted (rule 7). |
| HorizonFit (1) | `else 0.0` → `1.0` | **A wallet with median hold 0 would get full horizon fit.** No zero-hold test. |
| Drawdown (1) | `return_frac or 0.0` → `1.0` | trips with an undefined return (zero entry notional) |
| EquitySlice (1) | `truncated=False` default → `True` | The default is never observed. |
| AsOfView (3) | see F3-2 | the `== t` boundary in `fills_between` / `candles_between` / `score` |
| LotBook (5) | `PositionGap` `time_ms`/`tid` → `None`; `_open_trips.pop(symbol)` → `pop(None)`; orphan trip `side` → `None`; orphan `entry_notional` kept | The gap evidence fields are never asserted. **After a gap to flat, the stale open trip is kept by the mutant, and no test checks the round trips after a gap.** |
| mathx (6) | `clamp` finite-check without `lo` or `hi`; `lo > hi` → `>=`; `safe_div` finite-check without `b` or `default`; `wilson` `z <= 0` → `<= 1` | NaN in `lo`/`hi`/`b`/`default`; `clamp(x, a, a)`; `0 < z < 1` |

**Validator survivors (93, not individually re-run):** almost all are *equality boundaries*
of parameter validation. Examples:
- `epsilon == 0` accepted (`combiner.__init__`);
- `min_trust ∈ {0, 1}`, `min_wallets == 1` (corroboration);
- `max_reversal_rate ∈ {0, 1}`;
- `min_oi_frac == 0`, `min_move == 0`;
- `Candle.volume == 0`, `n_trades == 0`.

One matters beyond validation. **`TickerSignal` accepting `score = ±1` exactly is
untested** (`-1.0 <= score` → `<` and `score <= 1.0` → `<` both survive). The combiner
produces exactly ±1 whenever every component saturates, so a regression here would crash
the run on the strongest possible signal.

---

## Phase 7 — Determinism

**Cross-process runs with different hash seeds.** Within one process, string hashing is
fixed, so the in-process check `test_snapshot_is_deterministic` (test_pipeline.py:68-73)
cannot detect set-iteration order leaking into output. I rendered the end-to-end fixture
pipeline (`tests/app/test_pipeline.report()`: `ScenarioGateway` + fixed `AS_OF`) in
**separate processes** with `PYTHONHASHSEED` = 0, 1, 2, 3, 12345 and `random`, in all three
formats:

```
json: 5/5 identical to seed 0      md: 5/5 identical      txt: 5/5 identical
seed-0 json == tests/app/snapshots/pipeline.json (committed snapshot)
```

The same for the backtest (synthetic market, walk-forward with 2 folds, `backtest_to_json`
and `render_backtest`) with seeds 0, 1, 2 and `random`: **identical**.

**Static hunt for wall-clock leaks and randomness** across *all* of `src`, including the
spellings the AST guard misses (F2-2): `grep -rnE
"\b(now|utcnow|today|time_ns|monotonic|perf_counter|process_time)\(|time\.time\(|\.sleep\(|\brandom\b|uuid|secrets\.|os\.urandom"`.
Every hit outside `core/clock.py` goes through an injected `clock` or `sleeper`:
`app/cli.py`, `app/dashboard.py:65`, `infra/transport.py:112/174/193/221`,
`infra/tape_feed.py:87`, `wallets/sources/curated.py:53`. There is no randomness anywhere in
`src`. **PROVEN** for the exercised paths.

- **F7-1 GAP (minor) — a curated wallet without `as_of` gets `clock.now()`**
  (`curated.py:53`). This is deterministic under an injected clock. In a backtest, though,
  the record's prior score is then dated *today*, so `AsOfView.score` hides it at every past
  `t` (asof.py:79). A curated Copy Score silently never contributes to a backtest. That is
  conservative, not a leak, but it is invisible: no caveat says so.
- Limitation: determinism is proven for the scenarios above. A path that no scenario
  reaches (for example the dashboard's reading of the sqlite files) is not covered.

---

## Supplementary checks (Section 2 rules and Section 5 rows not covered above)

- **Rule 3 (no Singletons).** An AST scan of every module's top level for
  `Name = CapitalizedCall(...)` (excluding value types) and for `global` statements found
  **none**. PROVEN.
- **Rule 6 (no magic numbers).** Every numeric literal other than 0/±1 in the logic packages
  (signals, wallets/scoring, filters, domain, backtest, universe, session, mathx) is a unit
  conversion (`hold * 24` h, `_BPS = 10_000`), a formula constant (Wilson `2*n`, `4*n*n`;
  decay `0.5 **`), or a named structural constant (`TRADING_DAYS_PER_YEAR = 252`,
  `_SATURDAY = 5`, `_EXIT_SEARCH_FACTOR = 2`). No tuning threshold is a literal. PROVEN.
  Not enforced by any gate (ruff's `PLR2004` is active, which is why `symbols.py:37` needs a
  noqa, but it does not flag named module constants).
- **Rule 8 (no network in unit tests).** **GAP: convention only.** No conftest blocks
  sockets: `grep` for `socket|disable_socket|pytest_socket` in every `conftest.py` found
  nothing, and pytest-socket is not installed. A probe test that opens a TCP connection to
  1.1.1.1:443 under the project's pytest config **passed** (`1 passed in 1.21s`). The
  existing HTTP tests use `httpx.MockTransport` (test_transport.py:32-34), so no current
  test goes online, but nothing stops one from doing so.
- **Rule 9 (domain models frozen).** An AST scan of every `@dataclass` in `src` found only
  `domain/lots.py:105 _Trip` and `:114 _OpenLot` not frozen. Both are private mutable
  accumulators inside `LotBook`, not domain models. PROVEN.
- **§5 rows, re-implementation grep** (outside each single home):
  - clamp (`min(max(`): none. shrinkage/decay formulas: none. `/info` payload `"type"`
    literals: none outside `infra/gateway.py`. Retry/backoff: none outside
    `infra/transport.py`. FIFO (`deque`/`popleft`): only the rate limiter's window in
    `transport.py:161`, which is unrelated. PROVEN.
  - **F8-1 GAP — equity restriction is re-implemented outside `wallets/scoring/slice.py`.**
    `app/vet.py:81` (`[f for f in head if f.symbol in equities]`, the prescreen sample)
    and `backtest/signal_source.py:159` (`traded = frozenset(... if f.symbol in
    self._equities)`) filter fills to equities themselves. Registry row "restricting a
    wallet's fills/positions to equity coins → slice.py".
  - **F8-2 GAP — the direction boundary is evaluated twice.** `signals/engine.py:50`
    recomputes `abs(score) > self.combiner.epsilon` to word the reason. The decision itself
    is made in `combiner.py:37` (`<=` → flat). If one side changes, the reason text
    contradicts the direction. Registry row "signal weighting and direction decision →
    combiner.py".
  - `infra/yahoo.py:23` has its own `ZoneInfo("America/New_York")` to convert Yahoo bar
    timestamps to dates. This is timestamp conversion, not a session rule. Noted, not a
    violation.

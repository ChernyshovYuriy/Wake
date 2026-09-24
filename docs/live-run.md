# Live run log

## 2026-09-24: first end-to-end live run (Phase 8 Definition of Done)

**Command**

```
hlsignals census --minutes 15            # 15:03-15:18 local (19:03-19:18 UTC)
hlsignals -v run --format json --output live_report.json
```

Default configuration (`config/example.toml` values), empty curated list, census source
with `min_observations = 20`.

**Census**: 14,569 trades on 88 US-stock markets; 1,033 distinct wallets
(483 with >= 5 trades, 202 with >= 20, 38 with >= 200).

**Run** (as of 2026-09-24 20:17 UTC, cash session closed): exit 0, 40 min 38 s wall
clock, peak RSS 1.1 GB.

| step | result |
|---|---|
| universe | 80 US-stock markets discovered, 46 after the $1M volume / $250k OI filters (34 rejected for volume); no unclassified symbols, no dex failures |
| wallets | 202 considered, **7 accepted**; rejected: maker_profile 176 (most by the first-page prescreen), min_sample 14, api_error 5; 20 histories hit the API cap |
| signals | **0 scored**: all 46 tickers INSUFFICIENT |

**Why no signals.** The 7 accepted wallets do hold positions in the universe (e.g. SKHY: 4
wallets, 3 long; NVDA, SNDK, MU, CRCL: 3 each), yet corroboration counted 0 trusted
wallets everywhere. So every accepted wallet scored trust < `min_trust` (0.4). This is
inferred from the counts: that report did not list wallet trust. Fixed afterwards: reports
now include an "Accepted wallets" section with trust = base x confidence x decay and the
feature evidence, and corroboration reasons show "N involved".

**What the run showed (and what changed because of it)**

1. A tape census mostly finds market makers: 176 of 202 wallets were rejected as makers.
   Swing wallets need a curated list, a longer census, or a lower `min_observations`.
2. Throughput: the first attempt vetted 10 wallets in 8-11 minutes (full 90-day
   histories plus candles for every traded symbol). Fixed with lazy candle fetching (only
   the reversal-bait filter reads candles, only for short trips) and a first-page
   fill-rate prescreen for heavy wallets (same fills/active-day limit as the maker filter).
   About 13x faster (~3-10 wallets/min).
3. Memory: an early attempt grew to 816 MB by keeping every rejected wallet's history
   (fixed: only accepted wallets keep theirs). This run still peaked at 1.1 GB, because
   `CachingTransport` never evicted expired responses. Fixed afterwards (expired entries are
   dropped on every call). **Not yet re-measured live.**
4. `min_trust = 0.4` with `shrinkage_k = 10` needs roughly 15+ round trips at good quality
   to be reached. The thresholds are starting defaults, to be tuned in the Phase 9 backtest.

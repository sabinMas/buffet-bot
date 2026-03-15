# Structural Audit — Buffet-Bot

> Owned by: Architect Agent
> Last audited: 2026-02-28 (original) — superseded note added 2026-03-06
> Audited against: `buffet_bot/main.py` (2760 lines), v0.4.0

> **NOTE (2026-03-06, Architect session 16):** The monolith was split in session 10 into 13 focused modules. The summary metrics below are now historical. Current structure is documented in the "Module Health" section further down — updated here. All D-00x debt items from the original audit were resolved during sessions 6–10 (D-001 asyncio removed, D-004 crypto migrated, D-006 split executed). The main entry point `main.py` is now a 97-line slim dispatcher.

---

## Summary (historical — v0.4.0 pre-split)

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| `main.py` total lines | 2760 | 3000 (re-eval per ADR-005) | 🟡 240 lines from limit |
| Sub-modules spawned | 4 (`crypto`, `politicians`, `volatile`, `ibkr`) | — | ✅ |
| Total functions in `main.py` | 72 | — | — |
| CLI commands | 25 (including `watchlist` subgroup) | — | ✅ |
| Dead imports | 1 (`asyncio`, line 3) | 0 | 🔴 Fix |
| Missing schema docs | 1 (`watchlist` table) | 0 | 🟡 Updated below |

---

## Domain Breakdown (line ranges, approximate)

| Domain | Lines | Range | Verdict |
|--------|-------|-------|---------|
| Imports + constants | 97 | 1–97 | ✅ Clean |
| DB layer (`init_db`, helpers, watchlist fns) | 112 | 98–209 | ✅ Clean, self-contained |
| CLI group declaration | 8 | 211–218 | ✅ |
| Data fetching (Buffett metrics, tech indicators, realtime, news, sentiment) | 192 | 219–410 | ✅ |
| LLM + analysis core (`_run_analysis`, `_query_llms_freeform`, `_place_order`) | 157 | 411–529 | 🟡 `_run_analysis` is 104 lines — complex |
| UI helpers (`_print_ai_responses`, `_consensus_text`, `_print_live_market`) | 40 | 530–569 | ✅ |
| Plan management + guide wizard | 267 | 570–837 | 🟡 Growing, 6 helper functions |
| Projections engine (Monte Carlo, FV, milestones helpers) | 119 | 838–956 | ✅ |
| Risk management (ATR, position sizing, sell signals, sector) | 116 | 957–1073 | ✅ |
| Technical analysis + backtest | 130 | 1074–1203 | 🟡 `_run_backtest` is 104 lines |
| Crypto analysis helper (`_analyze_crypto`) | 131 | 1204–1334 | 🟡 Belongs in `crypto.py` long-term |
| CLI commands layer (all `@cli.command()` functions) | 1473 | 1335–2807 | ✅ Expected for CLI app |

---

## Debt Items (Prioritized)

### 🔴 P1 — Fix Immediately

#### D-001: Dead `asyncio` import (line 3)
- `import asyncio` on line 3 is never called anywhere in `main.py`
- It was imported in anticipation of async LLM queries (see open question in DECISIONS.md)
- **Action:** Remove `import asyncio` until async is actually implemented. Dead imports increase reader confusion about the codebase's actual capabilities.
- **Owner:** ENG (trivial 1-line change)

---

### 🟡 P2 — Address This Milestone

#### D-002: `watchlist` table missing from SCHEMA.md
- `init_db()` (line 126) creates a `watchlist` table with `(ticker TEXT PRIMARY KEY, added_at TEXT)`.
- SCHEMA.md only documents `recommendations` and `outcomes`.
- **Action:** Update SCHEMA.md (done in this audit). Also update Migration Log.
- **Owner:** ARCH (this file)

#### D-003: CLI subgroup pattern not in PATTERNS.md
- `watchlist` is a Click subgroup (`@cli.group()` + `@watchlist.command()`), a new pattern in this codebase.
- No other agent or the ENG has documented how to correctly declare subgroups.
- **Action:** Add Pattern #15 to PATTERNS.md (done in this audit).
- **Owner:** ARCH (this file)

#### D-004: `_analyze_crypto` belongs in `crypto.py`
- `_analyze_crypto()` (lines 1204–1334, 131 lines) is the main analysis logic for crypto — it calls Ollama, formats output, and orchestrates crypto-specific data fetching.
- It lives in `main.py` but is logically part of the crypto module.
- Moving it would reduce `main.py` by ~131 lines and keep crypto logic co-located.
- **Risk:** The function uses `console`, `MODELS`, `MODEL_COLORS`, `STRATEGY_PROMPTS` from `main.py` — these would need to be passed as parameters or imported.
- **Verdict:** Worth doing in v0.4.1. Propose to PM before ENG executes.
- **Owner:** ARCH (proposal) → PM (approval) → ENG (implementation)

---

### 🟢 P3 — Watch / Plan For

#### D-005: Config file design needed for v0.4.1
- ROADMAP v0.4.1 lists `~/.buffet-bot-config.toml` for user preferences.
- Design is documented in ADR-008 (below). ENG should not implement until the ADR is accepted.

#### D-006: Split candidates when `main.py` hits 3000 lines
When the line count crosses 3000, the following splits are pre-approved for proposal:

| Module | What moves there | Estimated lines | Risk |
|--------|-----------------|-----------------|------|
| `db.py` | `init_db`, `log_recommendation`, `get_recent_recommendations`, `add_to_watchlist`, `remove_from_watchlist`, `get_watchlist` | ~112 | Low — no callers outside main.py except themselves |
| `projections.py` | `_calculate_future_value`, `_get_ai_expected_return`, `_get_portfolio_expected_return`, `_run_monte_carlo`, `_display_mc_chart`, `_years_to_reach` | ~119 | Low — used only by `forecast`, `whatif`, `scenarios`, `milestones` commands |
| `analysis.py` | `get_buffett_metrics`, `get_tech_indicators`, `analyze_news_sentiment`, `_compute_rsi`, `_calculate_sharpe`, `_calculate_max_drawdown`, `_run_backtest`, `_get_atr`, `_calculate_position_size`, `_check_sell_signals`, `_show_sector_table` | ~460 | Medium — many callers in CLI commands |

**Do not split yet.** Raise proposals with PM when line count crosses 3000.

---

## Functions Over 80 Lines (Complexity Watch)

| Function | Lines | Location | Notes |
|----------|-------|----------|-------|
| `_run_analysis()` | ~104 | 411–515 | Central orchestrator — complexity is warranted, but watch for further growth |
| `_run_backtest()` | ~104 | 1100–1203 | Isolated backtesting logic — good candidate for `analysis.py` extraction later |
| `_analyze_crypto()` | ~131 | 1204–1334 | Should migrate to `crypto.py` (D-004) |
| `forecast` command | ~98 | 1880–1977 | Heavy Monte Carlo + display logic — consider `_run_forecast()` helper extraction |
| `whatif` command | ~89 | 1986–2074 | Interactive calculator — acceptable |
| `news` command | ~84 | 2590–2673 | Multi-source aggregation — acceptable |

---

## Module Health: Sub-Modules

> Updated 2026-03-06 by Architect Agent (session 16) to reflect post-split state.

| Module | Exists? | Clean separation? | Notes |
|--------|---------|------------------|-------|
| `globals.py` | ✅ | ✅ | Constants, API clients, config, theme, LIVE_MODE |
| `db.py` | ✅ | ✅ | SQLite helpers; calls `init_live_audit_table()` from `live_guard` |
| `data.py` | ✅ | ✅ | yfinance fetchers, FRED macro, multiframe signals, analyst consensus |
| `analysis.py` | ✅ | ✅ | `_run_analysis()`, LLM querying, Buffett scoring, news sentiment |
| `backtest.py` | ✅ | ✅ | `_run_backtest()`, RSI strategy, SPY benchmark |
| `risk.py` | ✅ | ✅ | `_get_atr()`, `_calculate_position_size()`, VaR |
| `projections.py` | ✅ | ✅ | Monte Carlo, `_calculate_future_value()` |
| `plans.py` | ✅ | ✅ | Investment plan save/load/schedule, wired with `confirm_live_execution` |
| `display.py` | ✅ | ✅ | Shared Rich display helpers |
| `cmd_trading.py` | ✅ | ✅ | analyze, buy, scan, status, compare, explain, SPY benchmark overlay |
| `cmd_intel.py` | ✅ | ✅ | news, insiders, crypto (display), volatile, options |
| `cmd_portfolio.py` | ✅ | ✅ | rebalance, backtest, sectors, var, forecast, whatif, scenarios, milestones |
| `cmd_account.py` | ✅ | ✅ | guide, plans, automate, config, alerts, watchlist, beats, completion |
| `live_guard.py` | ✅ | ✅ | Triple-confirmation safety layer; `live_audit` table; **untracked in git — needs `git add`** |
| `crypto.py` | ✅ | ✅ | `_analyze_crypto` migrated here (D-004 resolved); wired with `confirm_live_execution` |
| `politicians.py` | ✅ | ✅ | House Stock Watcher S3 + FMP API |
| `volatile.py` | ✅ | ✅ | 75-ticker universe volatility scanner |
| `ibkr.py` | ✅ | ✅ | IBKR EWrapper/EClient sync wrapper |
| `automate.py` | ✅ | ✅ | ReAct agent loop (no circular imports — injected dependencies) |
| `universe.py` | ✅ | ✅ | 366-company DB across 11 GICS sectors |
| `insiders.py` | ✅ | ✅ | SEC EDGAR Form 4 fetcher |

### Planned Modules (architecture designed, ENG not started)

| Module | ADR | Blocks |
|--------|-----|--------|
| `edge.py` | ADR-013 | v0.7.0 ENG items, `options_engine.py` CSP filter |
| `options_engine.py` | ADR-014 | v0.8.0 ENG items |
| `macro.py` | ADR-015 | v0.9.0 ENG items |

---

## Performance Baseline — `analyze` End-to-End Wall Time

> Added: 2026-03-04 by Software Engineer Agent (session 14)
> Based on: static analysis of `analysis.py` + `data.py` (no live profiling run yet)

### Phases and Theoretical Timing

The `analyze` command's critical path runs in two sequential phases:

**Phase 1: Concurrent I/O fan-out** (`ThreadPoolExecutor(max_workers=9)`)

| Worker | Operation | Typical latency |
|--------|-----------|-----------------|
| `f_hist` | `yf.download(ticker, period='6mo')` | 0.8–2.0 s |
| `f_buffett` | `get_buffett_metrics(ticker)` — `yf.Ticker.info` via `_yf_semaphore(2)` | 1.0–2.5 s |
| `f_realtime` | Alpaca `StockLatestQuoteRequest` + `StockLatestBarRequest` | 0.2–0.6 s |
| `f_news` | Alpaca News API HTTP GET | 0.3–0.8 s |
| `f_macro` | `_fetch_fred_data()` — 3 FRED requests concurrently (inner `ThreadPoolExecutor(3)`) | 0.5–1.5 s |
| `f_insiders` | SEC EDGAR Form 4 HTTP fetch | 0.5–2.0 s |
| `f_multiframe` | `get_multiframe_signals()` — `yf.download(period='1y')` + resample | 1.0–2.5 s |
| `f_analyst` | `get_analyst_consensus()` — `yf.Ticker.info` + upgrades_downgrades | 1.0–2.5 s |
| `f_tech` (high risk only) | `get_tech_indicators()` — `yf.download(period='3mo')` | 0.8–1.8 s |

**Phase 1 wall time = max of the above workers** = ~2.5 s (typical) / ~4.0 s (slow network)

Note: `get_buffett_metrics`, `get_multiframe_signals`, and `get_analyst_consensus` all call `yf.Ticker` and are gated by `_yf_semaphore(2)` (max 2 concurrent yfinance sessions). This is the current bottleneck within the concurrent phase.

**Phase 2: Sequential LLM inference**

| Step | Operation | Typical latency |
|------|-----------|-----------------|
| Sentiment | `analyze_news_sentiment()` — 1 Ollama chat call | 3–10 s |
| Primary LLM | `ollama.chat(deepseek-r1, ...)` | 5–20 s |
| Secondary LLM | `ollama.chat(qwen2.5:7b, ...)` | 3–12 s |

**Phase 2 wall time = sum of the above** = ~11–42 s (hardware-dependent)

### Total Baseline

| Scenario | Estimated wall time |
|----------|---------------------|
| Low risk, warm Ollama, fast network | ~14 s |
| High risk, cold Ollama models, slow network | ~50 s |
| **Typical production run** | **~20–30 s** |

### Bottleneck Analysis

1. **Primary bottleneck: Ollama LLM inference (Phase 2)** — accounts for 75–85% of total wall time. Not parallelisable without architectural changes (each LLM call depends on prior context or is intentionally sequential for consensus voting).
2. **Secondary bottleneck: yfinance semaphore contention (Phase 1)** — `_yf_semaphore(2)` limits concurrent yfinance sessions to prevent crumb/DB lock errors. The 3 concurrent yf-based workers (`f_buffett`, `f_multiframe`, `f_analyst`) serialise into pairs, adding ~0.5–1.5 s to Phase 1.
3. **Not a bottleneck: Alpaca and FRED** — these are fast REST calls (<1 s each) that complete well before the yfinance workers.

### Optimisation Candidates (for future sessions)

| Candidate | Expected saving | Risk |
|-----------|----------------|------|
| Increase `_yf_semaphore` to 3 | ~0.5–1.0 s on Phase 1 | Medium — original 2-semaphore was set to prevent yfinance session corruption; needs testing |
| Cache `yf.Ticker.info` across concurrent workers that call it for the same ticker | ~0.5–1.5 s | Low — all three workers use same ticker; a simple `functools.lru_cache(maxsize=32)` on a wrapper would help |
| Parallel LLM queries (send both prompts simultaneously) | ~5–12 s | Medium — LLM is CPU/GPU-bound; parallel inference may degrade response quality; needs hardware profiling |

### Live Profiling Instructions (for QA/PERF agent)

To capture a real baseline:
```bash
python -c "
import time, sys
sys.argv = ['buffet-bot', 'analyze', 'AAPL']
start = time.perf_counter()
from buffet_bot.main import cli
cli(standalone_mode=False)
print(f'Wall time: {time.perf_counter() - start:.2f}s')
"
```
Or use `time python buffet-bot.py analyze AAPL` for a coarse measurement.

---

## Next Audit Trigger

Re-run this audit when:
- Any module exceeds 600 lines (current largest: `cmd_trading.py`, estimated ~500 lines)
- Any single domain function exceeds 150 lines
- `edge.py`, `options_engine.py`, or `macro.py` are implemented (add them to the module health table)
- A v0.7.0 ENG session adds `edge_scans` table or modifies `analysis.py` fan-out

**A full re-audit is NOT needed for v0.5.0 → v0.6.0 ENG work** (compound tables are additive to `db.py`; `compound` command adds to `cmd_portfolio.py`).  Trigger a re-audit at v0.7.0 milestone when `edge.py` lands.

---

## v0.9.0 Macro FRED Cache Audit (2026-03-15)

> Auditor: Performance Engineer Agent
> Scope: Thread-safety of `_fred_cache` and `_regime_cache` under concurrent access from `analysis.py` fan-out and `edge-scan --macro` multi-ticker fan-out.

### Architecture Overview

There are **two layers of caching** for FRED/macro data:

1. **`_fred_cache`** in `data.py` (line 207): Module-level dict `{"data": {}, "ts": 0.0}` with a 5-minute TTL (`_FRED_TTL = 300`). Caches raw FRED API responses (fed_rate, yield_curve, cpi). No lock.
2. **`_regime_cache`** in `macro.py` (line 138): Module-level dict `{"ts": 0.0, "data": None}` with a 5-minute TTL (`_REGIME_CACHE_TTL = 300`). Caches the classified regime result (regime, confidence, signals). **Protected by `_regime_cache_lock = threading.Lock()`**.

### Does `macro.py` use `_fetch_fred_data()` from `data.py` or make its own HTTP calls?

`macro.py` imports `_fetch_fred_data` from `data.py` (line 26: `from buffet_bot.data import _fetch_fred_data`). It does **not** make any direct HTTP calls to FRED. The only FRED HTTP traffic originates from `data._fetch_fred_data()`. All macro functions (`get_macro_regime`, `compute_macro_score`, `macro_prompt_block`, `get_recession_probability`) call `get_macro_regime()` which calls `_fetch_fred_data()` exactly once per cache miss.

### Call Graph Analysis

**Single `analyze` call** (`analysis.py:_run_analysis`, line 45):

The 10-worker `ThreadPoolExecutor` fan-out submits both:
- `f_macro = ex.submit(_fetch_fred_data)` (line 59) -- direct FRED data fetch
- `f_macro_prompt = ex.submit(macro_prompt_block, ticker)` (line 63) -- which internally calls `get_macro_regime()` -> `_fetch_fred_data()`

Additionally, after the fan-out completes, line 222 calls `compute_macro_score(ticker)` which calls `get_macro_regime()` -> `_fetch_fred_data()`.

So within a single `analyze` call, `_fetch_fred_data()` can be called up to **3 times concurrently/sequentially**:
1. Directly from the fan-out (`f_macro`)
2. Indirectly via `macro_prompt_block` -> `get_macro_regime()` (in the fan-out, concurrent with #1)
3. Via `compute_macro_score` -> `get_macro_regime()` (after fan-out completes)

**`edge-scan --macro`** (`cmd_intel.py:edge_scan`, line 442):

The macro scoring phase (lines 527-539) uses a `ThreadPoolExecutor(max_workers=4)` to call `compute_macro_score(t)` for all tickers concurrently. Each call invokes `get_macro_regime()` -> `_fetch_fred_data()`.

### Thread-Safety Assessment

#### `_regime_cache` in `macro.py` -- SAFE

The regime cache is protected by `_regime_cache_lock` (a `threading.Lock`). Both `_get_cached_regime()` and `_set_cached_regime()` acquire the lock before reading or writing. Under concurrent access:
- The first thread to find the cache empty will proceed to call `_fetch_fred_data()`.
- Other threads may also find the cache empty (TOCTOU gap between the lock release in `_get_cached_regime` and lock acquisition in `_set_cached_regime`), resulting in redundant FRED calls, but the cache will be populated correctly and subsequent calls will hit the cache.
- There is no data corruption risk. The TOCTOU gap causes at most N redundant FRED calls where N is the number of concurrent threads that check the cache before the first one populates it.

#### `_fred_cache` in `data.py` -- TECHNICALLY UNSAFE, PRACTICALLY SAFE

The `_fred_cache` dict in `data.py` has **no threading.Lock**. The check-and-update pattern (lines 219-251) is:

```python
now = time.monotonic()
if _fred_cache["data"] and now - _fred_cache["ts"] < _FRED_TTL:
    return _fred_cache["data"]    # cache hit
# ... fetch from FRED ...
_fred_cache["data"] = result      # cache update
_fred_cache["ts"] = time.monotonic()
```

This is a classic TOCTOU (time-of-check-time-of-use) race. However, it is **practically safe** for these reasons:

1. **CPython GIL**: Dict key assignment (`_fred_cache["data"] = result`) is atomic under the GIL. No thread will read a partially-written dict value. The worst case is that two threads both miss the cache, both fetch from FRED, and both write their results -- the last writer wins, but the data is identical (same 3 FRED series, fetched within milliseconds of each other).

2. **No mutation of cached values**: The cached `result` dict is a fresh dict created inside `_fetch_fred_data()`. Callers only read its values; nobody mutates the returned dict in place.

3. **Monotonic clock**: `time.monotonic()` is thread-safe and monotonically increasing, so the TTL check cannot be fooled by clock adjustments.

4. **Benign duplicate work**: If 4 threads all miss the cache simultaneously, they each spin up their own `ThreadPoolExecutor(max_workers=3)` to fetch the 3 FRED series. This means up to 12 HTTP requests to FRED instead of 3. While wasteful, it is:
   - Correct (all threads get valid data)
   - Bounded (FRED rate limit is 120 requests/minute for free keys; 12 requests is well within limit)
   - Self-healing (after the first cache population, all subsequent calls within 5 minutes will hit the cache)

### Expected FRED HTTP Calls by Scenario

| Scenario | FRED HTTP calls (worst case) | FRED HTTP calls (typical) | Notes |
|----------|------------------------------|---------------------------|-------|
| Single `analyze` call | 9 (3 calls x 3 series) | 3 | Two concurrent fan-out tasks + one post-fan-out call; cache usually populated by first to complete |
| `edge-scan --macro` with 20 tickers | 12 (4 threads x 3 series) | 3 | 4-worker pool; first thread populates cache, remaining 19 tickers hit cache |
| `edge-scan --macro` with 20 tickers (cold cache) | 12 | 3 | Same as above; max 4 concurrent threads means at most 4 cache misses before first write |

### Identified Issues

#### ISSUE-1: Redundant `_fetch_fred_data()` call in `analysis.py` fan-out (LOW severity)

In `_run_analysis()`, both `f_macro = ex.submit(_fetch_fred_data)` (line 59) and `f_macro_prompt = ex.submit(macro_prompt_block, ticker)` (line 63) fetch FRED data. The `f_macro` result is used to build `macro_block` (lines 112-117), while `f_macro_prompt` builds a richer prompt string that includes the same data plus regime classification and sector rotation.

The `macro_block` built from `f_macro` is a subset of the information in `macro_prompt_str` built from `f_macro_prompt`. Both are concatenated into the LLM prompt. This means:
- The LLM prompt contains partially-redundant macro information (raw FRED values AND classified regime)
- One of the two FRED fetches is unnecessary if the cache is cold (though the second will hit cache)

This is a prompt bloat issue more than a performance issue, since the cache makes the second call fast.

#### ISSUE-2: `compute_macro_score(ticker)` called after fan-out without using cached results (LOW severity)

At line 222 of `analysis.py`, `compute_macro_score(ticker)` is called after the fan-out completes. This call goes through `get_macro_regime()` -> `_fetch_fred_data()`, which will always hit the `_fred_cache` at this point (populated during the fan-out). However, the macro regime was already computed inside `macro_prompt_block` during the fan-out. Reusing the regime data from `f_macro_prompt.result()` would avoid the redundant `get_macro_regime()` call chain, though the performance impact is negligible since everything is cached.

#### ISSUE-3: No `threading.Lock` on `_fred_cache` in `data.py` (INFORMATIONAL)

As analyzed above, the lack of a lock is practically safe under CPython's GIL, but it is:
- Not safe under alternative Python implementations (PyPy with STM, GraalPy, or future GIL-free CPython via PEP 703)
- Inconsistent with `macro.py` which correctly uses `_regime_cache_lock`

Adding a `threading.Lock` to `_fred_cache` would cost negligible overhead and provide defense-in-depth.

### Double-Fetch and Cache Miss Scenarios

| Scenario | Double-fetch risk? | Details |
|----------|-------------------|---------|
| Single `analyze`, warm cache | No | All 3 call sites hit `_fred_cache` and `_regime_cache` |
| Single `analyze`, cold cache | Minor | `f_macro` and `f_macro_prompt` race to populate `_fred_cache`; one will fetch, one may also fetch before cache is written. At most 6 FRED HTTP requests instead of 3. |
| `edge-scan --macro` 20 tickers, warm cache | No | All `compute_macro_score` calls hit both caches |
| `edge-scan --macro` 20 tickers, cold cache | Minor | Up to 4 threads (pool size) may miss `_fred_cache` before first write. At most 12 FRED requests. `_regime_cache` has the same TOCTOU window but protected by lock -- still allows up to 4 redundant `_classify_regime` computations (CPU-cheap). |
| Sequential `analyze` calls within 5 min | No | Both caches hold valid data for 5 minutes |

### Recommendations

1. **LOW PRIORITY -- Add `threading.Lock` to `_fred_cache`**: Create a `_fred_cache_lock = threading.Lock()` in `data.py` and wrap the check-and-update in `_fetch_fred_data()` with it. This follows the same pattern already used in `macro.py` for `_regime_cache_lock`. Cost: ~10 lines. Benefit: future-proofs against GIL-free Python, eliminates redundant FRED requests under concurrent cold-cache scenarios.

2. **LOW PRIORITY -- Pre-warm FRED cache before fan-out**: In `cmd_intel.py:edge_scan()`, call `_fetch_fred_data()` once synchronously before the `ThreadPoolExecutor` loop (line 528). This guarantees the cache is warm when all 4 worker threads start calling `compute_macro_score()`. Cost: 1 line + ~1.5s added to sequential path (but saves the same time from worker threads). Benefit: eliminates all redundant FRED requests, reduces total FRED API usage from up to 12 to exactly 3 requests.

3. **INFORMATIONAL -- Redundant macro data in LLM prompt**: The `_run_analysis()` fan-out in `analysis.py` submits both `_fetch_fred_data()` (line 59) and `macro_prompt_block()` (line 63). The raw FRED data from `f_macro` is used to build `macro_block` which overlaps with the richer `macro_context_block` from `macro_prompt_block`. Consider removing `f_macro` from the fan-out and relying solely on `macro_prompt_block` for the LLM prompt. This would reduce prompt size by ~50 tokens and eliminate one redundant fan-out task.

### Verdict

The FRED cache mechanism is **functionally correct and performant** for the current use cases. The `_regime_cache` in `macro.py` is properly thread-safe with a lock. The `_fred_cache` in `data.py` lacks a lock but is safe under CPython's GIL with at-worst benign redundant HTTP calls. For an `edge-scan --macro` with 20 tickers, the system makes 3 FRED HTTP requests (typical) rather than 60 (3 series x 20 tickers), confirming the cache is effective.

No blocking issues. Recommendations 1-3 are quality improvements, not correctness fixes.

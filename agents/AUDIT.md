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

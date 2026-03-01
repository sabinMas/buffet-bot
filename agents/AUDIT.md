# Structural Audit — Buffet-Bot

> Owned by: Architect Agent
> Last audited: 2026-02-28
> Audited against: `buffet_bot/main.py` (2760 lines), v0.4.0

---

## Summary

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

| Module | Exists? | Clean separation? | Notes |
|--------|---------|------------------|-------|
| `crypto.py` | ✅ | Partial — `_analyze_crypto` still in main.py | See D-004 |
| `politicians.py` | ✅ | ✅ | Clean |
| `volatile.py` | ✅ | ✅ | Clean |
| `ibkr.py` | ✅ | ✅ | Clean |

---

## Next Audit Trigger

Re-run this audit when:
- `main.py` exceeds 3000 lines
- Any single domain function exceeds 150 lines
- The ENG adds a new sub-module
- A new v0.5.0 feature is scoped that adds >200 lines

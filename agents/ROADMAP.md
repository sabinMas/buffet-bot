# Buffet-Bot Roadmap

> Managed by: Product Manager Agent
> Last updated: 2026-03-01
> Current version: v0.4.1

---

## Active Role Assignment

> **FIRST AGENT EACH SESSION:** Read this block before choosing a role.
> Pick the role listed under "Next session". Do not free-roam into Security Auditor
> unless it is explicitly listed here — those items are gated to v1.0.0 pre-release.

| Session | Role | Status |
|---------|------|--------|
| 1–5     | Engineer (v0.4.x) | complete |
| 6       | Engineer (v0.5.0 staging) | complete |
| 7       | Engineer (v0.5.0 features) | complete |
| 8       | QA + Engineer (v0.5.0 bugfix) | complete |
| 9       | Engineer (v0.5.0 finish) | complete |
| **10 →**| **PM / Release Manager (v0.5.0 release)** | **next** |

**Current milestone:** v0.5.0 (all Automation + Risk + Signal items `[x]`; analyst ratings deferred to backlog)
**Do NOT take Security Auditor** until v1.0.0 milestone is explicitly started.
**Suggested focus for session 10:** bump `pyproject.toml` version `0.4.1` → `0.5.0`, write `CHANGELOG.md` v0.5.0 entry, tag `v0.5.0`, then start v1.0.0 planning.

---

## Session Handoff Log

### 2026-03-01 — Engineer (session 9)
**Role taken:** Engineer (v0.5.0 finish)
**What was done:**
- `scan --notify` + `--min-score`: plain-text cron/email report mode for `scan`
- `TAX_LOSS` signal in `_check_sell_signals(tlh_pct=5.0)` + `--tlh-threshold` on `check-sells`
- `run-plan` scheduler CLI wiring: `--schedule NAME FREQ`, `--run-due` on `plans` command; Schedule + Last Run columns in `plans` list; `_is_plan_due` / `_set_plan_schedule` / `_mark_plan_ran` helpers were already in main.py
- ROADMAP: all v0.5.0 Automation + Risk items marked `[x]`; analyst consensus ratings deferred to backlog
**Next:** PM/Release Manager — bump version to `0.5.0`, write CHANGELOG, tag release

---

### 2026-03-01 — Engineer (session 6)
**Role taken:** Engineer (v0.5.0)
**What was done this session:**
- Fixed `pyproject.toml` version: `0.2.0` → `0.4.1` (was stale since v0.2.0)
- Committed all untracked files: `buffet_bot/automate.py`, `buffet_bot/insiders.py`, `buffet_bot/universe.py`, `tests/` (4 test files)
- Marked `alerts check` as complete in ROADMAP (was already implemented in main.py at line 3594)
**Next agent role: Engineer (v0.5.0)** — do NOT take Security Auditor role; SEC items are deferred to v1.0.0 pre-release only.
**Suggested targets:**
- `[ENG]` Simulated tax-loss harvesting signal in `check-sells` (last open Risk item)
- `[ENG]` `run-plan` scheduler — execute saved plans on a schedule
- `[ENG]` Multi-timeframe analysis (1d/1w/1mo signals combined)
- `[ENG]` Earnings surprise tracker — log beat/miss history in SQLite
- `[QA]` Run `pytest tests/` and verify all tests pass before v0.5.0 is declared ready
**Done this session (session 6 continued):**
- Concurrent FRED + data fetch in `_run_analysis()` + `_fetch_fred_data()`
- Beta-adjusted position sizing (`get_buffett_metrics` returns beta; `_calculate_position_size` scales by beta)
- Portfolio VaR: `_calculate_portfolio_var()` + `buffet-bot var` command
- `scan --notify` + `--min-score` — plain-text cron/email report mode
- Simulated tax-loss harvesting — `TAX_LOSS` signal in `_check_sell_signals(tlh_pct=5.0)`, `--tlh-threshold` flag on `check-sells`, disclaimer footnote
**Known issues / notes:**
- `tests/` are staged but have not been run in CI — QA agent should verify pass rate
- `pyproject.toml` had `[project.optional-dependencies]` added in a prior session but version not bumped — now fixed
- `alerts check` was implemented but ROADMAP showed `[ ]` — now corrected
- All v0.5.0 Risk items are now complete (`[x]`); remaining open: Signals (3 items) and Performance (2 items)

---

## How to Read This File

- Labels: `[PM]` `[ENG]` `[ARCH]` `[STYLE]` `[SCRAPER]` `[QA]` `[PERF]` `[REL]` `[SEC]` — which agent owns it
- Status: `[ ]` pending · `[~]` in progress · `[x]` done
- Milestone: v0.4.0 · v0.5.0 · v1.0.0

---

## v0.4.0 — Crypto, Politician Intelligence, Volatile Scanner, Multi-Account ✅ SHIPPED

### Crypto
- [x] [ENG] `buffet_bot/crypto.py` — Alpaca crypto bars/quotes/volatility, Coinbase Advanced Trade orders
- [x] [ENG] `crypto [SYMBOL]` command — live dashboard for all 8 pairs, or full LLM analysis + optional order
- [x] [ENG] `analyze` auto-detects crypto symbols (BTC/USD etc.) and routes to crypto flow

### Politician Intelligence
- [x] [ENG] `buffet_bot/politicians.py` — House Stock Watcher S3 + FMP API, merge/dedup
- [x] [ENG] `news <TICKER>` command — Alpaca headlines + congressional trades + short interest + AI summary

### Volatile Scanner
- [x] [ENG] `buffet_bot/volatile.py` — 75-ticker universe, 0–100 score (beta/mktcap/short%/30d-vol)
- [x] [ENG] `volatile` command — concurrent ThreadPoolExecutor scan, `--n` and `--universe` flags

### Multi-Account
- [x] [ENG] `buffet_bot/ibkr.py` — synchronous IBKR EWrapper/EClient wrapper, account summary + orders
- [x] [ENG] `status` shows 3 panels: Alpaca paper + Coinbase + IBKR (all gracefully degrade if unconfigured)

### Architecture
- [x] [ARCH+ENG] Module split executed: `politicians.py`, `crypto.py`, `volatile.py`, `ibkr.py`
- [x] [ENG] `scan` replaced serial `time.sleep(1)` loop with `ThreadPoolExecutor` concurrent fetch
- [x] [ENG] `asyncio` + `concurrent.futures` imported into main.py for future async speedup

---

## v0.4.1 — Data Expansion + UX Polish ✅ SHIPPED

### Intelligence
- [x] [SCRAPER+ENG] SEC EDGAR Form 4 insider transaction fetcher + `insiders` command — complete 2026-03-01
- [x] [ENG] FRED macro indicators injected into LLM prompt — TICKET-001 complete 2026-03-01
- [x] [ENG] Earnings calendar integration — TICKET-002 complete 2026-03-01
- [x] [ENG] Options chain basic display — put/call ratio, unusual volume flag

### Portfolio
- [x] [ENG] `rebalance` command — compare actual allocation vs target, suggest trades
- [x] [ENG] Watchlist management — `watchlist add TSLA`, `watchlist remove TSLA`, `watchlist show`
- [x] [ENG] `alerts` command — set price/RSI thresholds, check on next run

### UX
- [x] [STYLE] Rich progress spinners on all LLM queries (no more blank wait)
- [x] [STYLE] Buffett score color coding: green >70, yellow 40-70, red <40
- [x] [STYLE] `scan` output: sort by score, color-coded rows, compact layout
- [x] [STYLE] Add data source + timestamp footer to `analyze` output
- [x] [ENG] `--json` flag on `analyze` and `scan` for scripting output
- [x] [ENG] Shell autocomplete via Click 8 built-in completion — `completion` command added 2026-03-01
- [x] [STYLE] `dashboard` command — column alignment, color thresholds, visual polish — complete 2026-03-01

### Architecture
- [x] [ARCH] Structural audit of main.py — `agents/AUDIT.md` written; split candidates documented; SCHEMA.md + PATTERNS.md updated
- [x] [ENG] Config file: `~/.buffet-bot-config.toml` — ADR-009 implemented; `config show` / `config init` commands added
- [x] [ENG] Remove dead `asyncio` import (line 3, main.py) — flagged in AUDIT.md D-001
- [x] [ENG] Migrate `_analyze_crypto()` from `main.py` to `crypto.py` — ADR-008 implemented; main.py -131 lines

### Discovery
- [x] [ENG] `buffet_bot/universe.py` — bundled 366-company DB across 11 GICS sectors; `list_companies()`, `search_companies()`, `search_edgar()` — complete 2026-03-01
- [x] [ENG] `browse` command — sector overview, sector filter (`--sector`), keyword search, full EDGAR 10K+ (`--all`) — complete 2026-03-01
- [x] [ENG] `_COMMON_TICKERS` expanded from 45 → 366 tickers sourced from `_COMPANY_DB` — tab-completion covers all sectors — complete 2026-03-01

---

## v0.5.0 — Automation + Advanced Signals

### Automation
- [x] [ENG] `automate` command — ReAct agent loop: LLM chains scan/analyze/buy tools autonomously to fulfill a natural-language goal; dry-run by default, `--execute` flag for paper trades, `--budget` cap, `--max-steps` limit — complete 2026-03-01
- [x] [ENG] Cron-compatible `scan --notify` mode: output parseable for scripts/email — complete 2026-03-01
- [x] [ENG] `run-plan` scheduler: `--schedule NAME FREQ` (daily/weekly/biweekly/monthly/off), `--run-due` for cron, Schedule+Last Run columns in `plans` list; `_is_plan_due()` / `_set_plan_schedule()` / `_mark_plan_ran()` helpers — complete 2026-03-01
- [x] [ENG] `alerts check` command: evaluate all set alerts and report — complete 2026-03-01

### Signals
- [ ] [SCRAPER+ENG] Analyst consensus ratings (Nasdaq API or similar)
- [x] [ENG] Multi-timeframe analysis: 1d, 1w, 1mo signals combined — `get_multiframe_signals()` (daily/weekly RSI-14, monthly SMA trend, 50-day SMA position) wired into `_run_analysis` concurrent fetch (max_workers 8); `multiframe_block` injected into LLM prompt — complete 2026-03-01
- [x] [ENG] Earnings surprise tracker: log beat/miss history in SQLite — `earnings_surprises` table, `log_earnings_result()`, `get_earnings_history()`; `beats log` / `beats show` CLI commands — complete 2026-03-01

### Performance
- [ ] [PERF] Resolve async LLM query open question — ThreadPoolExecutor vs asyncio ADR
- [ ] [PERF] Profile `analyze` end-to-end wall time; document baseline in AUDIT.md
- [x] [PERF] Concurrent FRED + Nasdaq HTTP calls — `_fetch_fred_data()` now uses ThreadPoolExecutor (3 parallel requests); `_run_analysis()` dispatches hist/buffett/tech/realtime/news/macro/insiders concurrently — complete 2026-03-01

### Risk
- [x] [ENG] Beta-adjusted position sizing — `get_buffett_metrics()` now returns `beta`; `_calculate_position_size()` accepts `beta` param and scales position down by `max(1.0, beta)` — complete 2026-03-01
- [x] [ENG] Portfolio VaR (Value at Risk) calculation — `_calculate_portfolio_var()` historical simulation (95%/99%); new `var` command — complete 2026-03-01
- [x] [ENG] Simulated tax-loss harvesting signal in `check-sells` — `TAX_LOSS` signal added to `_check_sell_signals(tlh_pct=5.0)`; `--tlh-threshold` flag on `check-sells`; disclaimer footnote — complete 2026-03-01

---

## v1.0.0 — Production-Grade CLI

### Intelligence
- [ ] [SCRAPER+ENG] SEC 10-K/10-Q filing fetcher — LLM summarizes key risks + financials
- [ ] [ENG] Multi-LLM model selection: allow pulling and using any Ollama model
- [ ] [ENG] Model performance tracking: log LLM recommendation outcomes in DB

### Architecture
- [x] [ARCH+ENG+QA] Full test suite with `pytest` — mock Alpaca and yfinance responses
  - [x] [QA] Phase 1: Pure logic tests (72 tests) — `tests/test_analysis.py` complete 2026-03-01
  - [x] [QA] Phase 2: DB layer tests — `tests/test_db.py` complete 2026-03-01
  - [x] [QA] Phase 3: Data fetching with mocks — `tests/test_data_fetching.py` complete 2026-03-01
  - [x] [QA] Phase 4: CLI commands via CliRunner — `tests/test_cli.py` complete 2026-03-01
- [ ] [ARCH] DB migration system: versioned schema changes

### Security
- [x] [SEC] Full credential handling audit — .gitignore, no hardcoded keys, no console leakage — complete 2026-03-01
- [x] [SEC+QA] SQL injection audit — all DB operations use parameterized queries — verified 2026-03-01
- [x] [SEC] Input validation audit — FINDING-001 path traversal in plan management fixed 2026-03-01 (see `agents/SECURITY-AUDIT.md`)
- [ ] [SEC] Dependency CVE scan (`pip-audit`) — fix any CVSS ≥7.0 before v1.0.0 release
- [x] [SEC] Data exfiltration audit — no cloud LLM calls in any code path — verified 2026-03-01

### Distribution
- [ ] [PM+ENG+REL] PyPI package: `pip install buffet-bot`
- [ ] [REL] Docker image with Ollama sidecar
- [ ] [PM+REL] Contribution guide and PR template
- [ ] [REL] CHANGELOG.md — full version history from v0.1.0

---

## Active Role Assignment

> Used by the Role Assignment Protocol in CLAUDE.md.
> The next session's first agent must read this table before choosing a role.

| Field | Value |
|-------|-------|
| **Next session role** | Software Engineer [ENG] |
| **Suggested focus** | v0.5.0 remaining items: `run-plan` scheduler, `alerts check` command, analyst consensus ratings |
| **Do NOT take** | Security Auditor (gated to v1.0.0 pre-release; audit is complete for this milestone) |
| **Last updated** | 2026-03-01 by Software Engineer Agent (Agent 3) |

---

## Session Handoff Log

### 2026-03-01 — Session 1 + 2

**Agent 1 (Software Engineer):** Implemented `buffet_bot/insiders.py` (SEC EDGAR Form 4), `buffet_bot/universe.py` (366-company DB + EDGAR live search), `buffet_bot/automate.py` (ReAct agent loop), and full `tests/` suite (phases 1–4, 4 files). Updated `main.py` to import and wire new modules. Updated `pyproject.toml` and `requirements.txt`. Also added `_safe_plan_path()` path traversal protection pre-emptively. Marked v0.4.1 and `automate` command as complete.

**Agent 2 (Security Auditor):** Audited all six security categories (credentials, SQL, shell injection, input validation, data exfiltration, XML). Found and documented FINDING-001 (path traversal, P1 — already fixed by Agent 1) and FINDING-002 (XML entity expansion, P3 — informational). Closed the residual gap where `_load_plan()` did not catch `ValueError` from `_safe_plan_path`. Wrote `agents/SECURITY-AUDIT.md`. Marked 4 of 5 v1.0.0 security checklist items complete. Remaining: `pip-audit` dependency CVE scan.

**Agent 3 (Software Engineer):** Implemented `scan --notify` mode (v0.5.0). Added `--notify` and `--min-score` flags to the `scan` command. Plain-text output: header, ranked table, BUY CANDIDATES list filtered by `--min-score` (default 60), cron-friendly footer. Rich spinner suppressed in notify mode. Updated ROADMAP item to `[x]`. Next focus: `run-plan` scheduler, `alerts check` command, analyst consensus ratings.

**Agent 4 (Software Engineer — session 7):** Confirmed TAX_LOSS harvesting signal was already implemented (`_check_sell_signals(tlh_pct=5.0)`). Added **earnings surprise tracker**: `earnings_surprises` SQLite table (UNIQUE on ticker+date), `log_earnings_result()` / `get_earnings_history()` helpers, `beats log` / `beats show` CLI commands with beat-rate summary. Added **multi-timeframe signals**: `get_multiframe_signals()` function computing daily RSI-14, weekly RSI-14 (via resample), monthly SMA-3/SMA-12 trend, and 50-day SMA position from 1-year data; wired into `_run_analysis` concurrent fetch (max_workers 7→8); `multiframe_block` injected into LLM prompt. Remaining v0.5.0 open items: `run-plan` scheduler, analyst consensus ratings.

**Agent 5 (QA + Engineer — session 8):** Ran full test suite — found 1 failure: `test_dotdot_only_blocked` in `tests/test_security.py`. Root cause: `_safe_plan_path('..')` did not raise because `'..' + '.json'` = `'...json'`, a literal filename that stays inside PLANS_DIR. Fix: added allowlist validation (only `[a-zA-Z0-9_-]`) before the pathlib check — two-layer defence. All **149 tests now pass**. Remaining open v0.5.0: `run-plan` scheduler, analyst consensus ratings.

---

## Backlog (Unscheduled)

- [ ] [ENG] Portfolio sector pie chart (plotext)
- [ ] [ENG] `compare AAPL MSFT` — side-by-side Buffett score comparison
- [ ] [SCRAPER] Reddit/WSB sentiment integration
- [ ] [SCRAPER] Google Trends signal (pytrends)
- [ ] [STYLE] Dark/light theme toggle (environment variable)
- [ ] [ENG] `explain` command — ask LLM to explain a specific metric or concept
- [ ] [PM] Public roadmap / GitHub Discussions

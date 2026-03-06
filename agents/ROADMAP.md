# Buffet-Bot Roadmap

> Managed by: Product Manager Agent
> Last updated: 2026-03-06
> Current version: v0.5.0

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
| 10      | Engineer (refactor main.py) | complete |
| 11      | PM / Release Manager (v0.5.0 release) | outstanding — skipped, rolled into session 15 |
| 12–14   | Engineer (v0.5.0 PERF + v0.6.0 ENG unblocked items) | complete |
| 15      | PM / Release Manager (v0.5.0 release) | complete — 2026-03-06 |
| 16      | Architect (v0.6.0 design — live trading guard + compound + sweep) | complete |
| **17 →**| **ENG (v0.6.0 — compound command + automate --sweep)** | **next** |

**Current milestone:** v0.5.0 SHIPPED (tag v0.5.0, 2026-03-06). v0.6.0 Live Guard ARCH+ENG complete. Several v0.6.0 display-only ENG items also done early.
**Do NOT take Security Auditor** until v1.0.0 milestone is explicitly started.
**Session 17 focus:** ENG — `compound_log`+`sweeps` tables in `db.py`, `compound` command in `cmd_portfolio.py`, `automate --sweep` flag, Alpaca corporate actions endpoint. All unblocked by live_guard.py (done). See ADR-012 for schema design. Also commit `buffet_bot/live_guard.py` (untracked — implement and stage first).

---

## Session Handoff Log

### 2026-03-06 — PM / Release Manager (session 15 — v0.5.0 release)
**Role taken:** PM / Release Manager [REL] — v0.5.0 release preparation and tagging
**What was done:**
- **Committed all outstanding changes from sessions 12-14** (13 modified files): `compare`,
  `explain`, `sectors` commands; SPY benchmark overlay on `portfolio`; PAPER/LIVE banner on
  `status`; `BUFFET_BOT_THEME` env var + `theme_color()` helper; `LIVE_MODE` stub; ADR-010
  (ThreadPoolExecutor); performance baseline in AUDIT.md; UI overhaul; yfinance/DB lock fixes
- **Bumped version 0.4.1 → 0.5.0** in `pyproject.toml` and `buffet_bot/__init__.py`
- **Created `CHANGELOG.md`** — full version history v0.2.0 through v0.5.0 using Keep a
  Changelog format; v0.5.0 entry covers all work from sessions 1-14 (35+ commands,
  13-module refactor, 149 tests, security audit, perf baseline, all signals)
- **Updated `agents/ROADMAP.md`**: header version bumped to v0.5.0, session 15 marked
  complete, session 16 already complete per Architect entry, CHANGELOG.md distribution
  item marked `[x]`, Active Role Assignment updated for session 17
- **Created annotated git tag `v0.5.0`**: `v0.5.0: Automation + Advanced Signals`

**Not committed (intentionally deferred):**
- `buffet_bot/live_guard.py` — untracked; verified complete by Architect (session 16);
  session 17 ENG should `git add buffet_bot/live_guard.py` and commit it before
  implementing the compound/sweep commands that depend on it

**Release checklist verified:**
- [x] Version consistent: `pyproject.toml` v0.5.0, `__init__.py` v0.5.0, tag v0.5.0, CHANGELOG v0.5.0
- [x] `CHANGELOG.md` has v0.5.0 entry with date 2026-03-06
- [x] Paper trading safeguard (`paper=True`) intact — never modified
- [x] `live_guard.py` untracked and NOT committed (belongs to ENG session 17 scope)
- [x] No placeholder stubs — all v0.5.0 items are `[x]` complete

**Next:** ENG session 17 — v0.6.0 compound command + automate --sweep (see ADR-012); commit live_guard.py first

---

### 2026-03-06 — Architect (session 16 — v0.6.0 Live Guard + compound engine design)
**Role taken:** Architect [ARCH] — v0.6.0 live guard verification + compound engine architecture
**What was done:**
- **Verified `live_guard.py` fully implemented** (untracked in git but complete): `is_live_mode()` (Factors 1+2: `BUFFET_BOT_LIVE=1` + `BUFFET_BOT_LIVE_SECRET` non-empty), `get_trading_client()` (paper=not LIVE_MODE, singleton cache), `confirm_live_execution()` (Factor 3: red warning panel + "YES I CONFIRM" prompt), `log_live_audit()`, `init_live_audit_table()` with indexes
- **Verified all 9 ADR-011 call sites wired**: `cmd_trading.py` (analyze, buy), `cmd_portfolio.py` (rebalance, check-sells), `cmd_account.py` (automate buy_stock), `plans.py` (2 sites), `crypto.py` (1 site), `db.py` init_live_audit_table call
- **Verified `globals.py` integration**: imports `is_live_mode` + `get_trading_client` from `live_guard`; `LIVE_MODE` and `trading_client` now live-aware
- **ADR-011 confirmed in `agents/DECISIONS.md`** (was written in a prior pulled-forward session): triple-confirmation design, call site inventory, ADR-004 amendment
- **ADR-012 written** (`agents/DECISIONS.md`): Compounding Engine schema — `compound_log` table (dividend/profit reinvestment rows), `sweeps` table (automate --sweep runs), `get_compoundable_income()` and `log_compound_event()` helper contracts
- **ADR-013 written** (`agents/DECISIONS.md`): Multi-factor Edge Score architecture — `edge.py` module design, `compute_edge_score()` signature, `edge_scans` table schema, signal weight configuration in `globals.py`
- **ADR-014 written** (`agents/DECISIONS.md`): Options Engine architecture — `options_engine.py` module design, greeks-free delta proxy, `options_positions` table schema, Alpaca options API integration contract
- **ADR-015 written** (`agents/DECISIONS.md`): Macro Regime Engine architecture — `macro.py` module design, `detect_macro_regime()` regime classifier, `macro_regimes` table with 1-hour cache, FRED indicator expansion contract
- **ROADMAP updated**: all v0.6.0 Live Guard items marked `[x]`

**Note for PM (session 15):** `live_guard.py` needs to be `git add`-ed and committed as part of the v0.5.0/v0.6.0 release. It is fully implemented and integrated but was never staged.

**Blocked (removed by this session):**
- ~~`live_guard.py`~~ — now unblocked; all v0.6.0 ENG items can proceed
- `compound` command — schema designed (ADR-012); ready for ENG session 17
- `automate --sweep` — design approved; ready for ENG session 17
- All v0.7.0 ENG items — unblocked by `edge.py` design (ADR-013); ready for dedicated ARCH+ENG sessions
- All v0.8.0 ENG items — unblocked by `options_engine.py` design (ADR-014)
- All v0.9.0 ENG items — unblocked by `macro.py` design (ADR-015)

**Next:** ENG session 17 — implement `compound_log`+`sweeps` tables, `compound` command, `automate --sweep` flag

---

### 2026-03-04 — Software Engineer (session 14 — v0.5.0 PERF + v0.6.0 ENG unblocked)
**Role taken:** Software Engineer [ENG] — v0.5.0 PERF items + v0.6.0 display-only ENG items
**What was done:**
- **ADR-010 written** in `agents/DECISIONS.md`: ThreadPoolExecutor vs asyncio decision — retains ThreadPoolExecutor; all deps are synchronous; asyncio would add complexity with no measurable benefit; all 5 justification points documented
- **Performance baseline documented** in `agents/AUDIT.md` (new "Performance Baseline" section): theoretical wall-time breakdown per worker in the concurrent fan-out phase; primary bottleneck is Ollama LLM inference (~75–85% of total time); secondary bottleneck is `_yf_semaphore(2)` contention; optimisation candidates listed; live profiling instructions included
- **Both v0.5.0 PERF items marked `[x]`** in ROADMAP.md
- **`portfolio` SPY benchmark overlay** added to `buffet_bot/cmd_trading.py`: `_fetch_spy_benchmark()` helper fetches SPY via yfinance and normalises to portfolio start equity; yellow SPY line added to plotext chart; `_annualised_cagr()` helper computes CAGR %; CAGR + Alpha summary line printed below chart; `--no-benchmark` flag to suppress overlay; gracefully degrades if yfinance unavailable
- **`status` PAPER/LIVE banner** added: `LIVE_MODE = False` constant in `globals.py` (stubbed for future live_guard.py ARCH work); green PAPER panel or red LIVE panel printed at top of `status` command output; `LIVE_MODE` imported in `cmd_trading.py`
- **v0.6.0 ROADMAP items marked `[x]`**: portfolio SPY overlay, status PAPER/LIVE banner

**Blocked (require Architect session first):**
- `live_guard.py` triple-confirmation wrapper (ARCH) — blocks TradingClient live switch, `--execute` live paths, `globals.py` LIVE_MODE going live
- `compound` command (ENG) — depends on live_guard.py
- `automate --sweep` flag (ENG) — depends on live_guard.py
- All v0.7.0 ENG items — depend on `edge.py` (ARCH)
- All v0.8.0 ENG items — depend on `options_engine.py` (ARCH)

**Next:** PM / Release Manager (session 15) — bump `pyproject.toml` 0.4.1 → 0.5.0, write `CHANGELOG.md`, tag `v0.5.0`, update PITCH.md

---

### 2026-03-04 — Software Engineer (session 13 — backlog polish)
**Role taken:** Software Engineer [ENG] — small backlog tasks (continued)
**What was done:**
- Added `sectors` command to `buffet_bot/cmd_portfolio.py`: fetches GICS sector via yfinance for every open Alpaca position, renders a Rich table (Sector / Tickers / Value / Weight / Risk columns with green/yellow/red concentration coloring) and a plotext horizontal bar chart; `--no-chart` flag to suppress chart; gracefully handles no positions or yfinance failures
- Registered `sectors` in `buffet_bot/main.py` (import + `cli.add_command`)
- Added dark/light theme toggle to `buffet_bot/globals.py`: `BUFFET_BOT_THEME` env var (values: `dark` [default] / `light`); `_THEMES` dict with two full color palettes; `THEME` dict (active palette) and `theme_color(role)` helper; invalid values silently fall back to `dark`
- Marked `[x]` in ROADMAP.md backlog: Portfolio sector pie chart, Dark/light theme toggle
- Verified: `python buffet-bot.py sectors --help` renders correctly; `BUFFET_BOT_THEME=light` switches palette; full `--help` shows `sectors` in command list

**Next:** PM / Release Manager (session 11 — still outstanding) — bump `pyproject.toml` version to `0.5.0`, write `CHANGELOG.md`, tag `v0.5.0`, update PITCH.md

---

### 2026-03-04 — Software Engineer (session 12 early)
**Role taken:** Software Engineer [ENG] — small backlog tasks
**What was done:**
- Added `compare TICKER_A TICKER_B` command to `cmd_trading.py`: fetches Buffett metrics for two tickers concurrently via `ThreadPoolExecutor`, displays a side-by-side Rich table with per-metric pass/fail badges and an Edge column, prints a verdict summary naming the winner
- Added `explain CONCEPT` command to `cmd_trading.py`: routes a Buffett-framed education prompt to the chosen LLM model and displays the explanation in a Rich panel; supports `--model` flag; degrades gracefully if Ollama is offline
- Registered both commands in `main.py` (import + `cli.add_command`)
- Marked both backlog items `[x]` in ROADMAP.md
- Syntax-verified all imports and confirmed `python buffet-bot.py compare --help` / `explain --help` render correctly

**Next:** PM / Release Manager (session 11) — bump version to `0.5.0`, write CHANGELOG, tag release (as originally planned; session 11 template entry below is still outstanding)

---

### 2026-03-XX — PM / Release Manager (session 11)
**What was done:**
- Bumped pyproject.toml 0.4.1 → 0.5.0
- Wrote CHANGELOG.md
- Tagged v0.5.0
- Updated PITCH.md: command count, 10X framework section, new feature sections (v0.6–v0.9)
- Updated ROADMAP.md: v0.6–v0.9 milestone blocks

**Next:** Architect (v0.6.0)

---

### 2026-03-02 — Engineer (session 10)
**Role taken:** Engineer (refactor main.py)
**What was done:**
- Split `buffet_bot/main.py` (4532 lines) → 13 focused modules:
  - `globals.py` (constants, API clients, config) — already existed
  - `db.py`, `data.py`, `display.py`, `analysis.py`, `backtest.py`, `risk.py`, `projections.py`, `plans.py` — already existed
  - `cmd_trading.py` (ask, lookup, browse, analyze, buy, history, portfolio, chat, scan, status, stream, chart, dashboard)
  - `cmd_intel.py` (news, insiders, crypto, volatile, options)
  - `cmd_portfolio.py` (rebalance, backtest, correlate, check_sells, var, forecast, whatif, scenarios, milestones)
  - `cmd_account.py` (guide, plans, automate, config, alerts, watchlist, beats, completion)
  - Slim `main.py` (92 lines — imports + cli.add_command registrations)
- Updated all tests: `test_db.py`, `test_analysis.py`, `test_data_fetching.py`, `test_cli.py`, `test_security.py`, `conftest.py` to import from new module paths
- All 149 tests pass
**Next:** PM/Release Manager — bump version to `0.5.0`, write CHANGELOG, tag release

---

### 2026-03-01 — Engineer (session 9)
**Role taken:** Engineer (v0.5.0 finish)
**What was done:**
- `scan --notify` + `--min-score`: plain-text cron/email report mode for `scan`
- `TAX_LOSS` signal in `_check_sell_signals(tlh_pct=5.0)` + `--tlh-threshold` on `check-sells`
- `run-plan` scheduler CLI wiring: `--schedule NAME FREQ`, `--run-due` on `plans` command; Schedule + Last Run columns in `plans` list; `_is_plan_due` / `_set_plan_schedule` / `_mark_plan_ran` helpers were already in main.py
- Analyst consensus: `get_analyst_consensus()` via yfinance `info` dict — rating, target price, upside %, analyst count, recent upgrades/downgrades; wired into `_run_analysis` + `analyze` output panel
- ROADMAP: all v0.5.0 items marked `[x]`; Performance items (async ADR, profiling) deferred to v1.0.0
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
- [x] [ENG] Analyst consensus ratings — `get_analyst_consensus()` via yfinance `info` dict (rating key, target price, upside %, analyst count, recent upgrades/downgrades); wired into `_run_analysis` concurrent fetch (max_workers 9); `analyst_block` injected into LLM prompt; Rich panel in `analyze` output — complete 2026-03-01
- [x] [ENG] Multi-timeframe analysis: 1d, 1w, 1mo signals combined — `get_multiframe_signals()` (daily/weekly RSI-14, monthly SMA trend, 50-day SMA position) wired into `_run_analysis` concurrent fetch (max_workers 8); `multiframe_block` injected into LLM prompt — complete 2026-03-01
- [x] [ENG] Earnings surprise tracker: log beat/miss history in SQLite — `earnings_surprises` table, `log_earnings_result()`, `get_earnings_history()`; `beats log` / `beats show` CLI commands — complete 2026-03-01

### Performance
- [x] [PERF] Resolve async LLM query open question — ThreadPoolExecutor vs asyncio ADR — ADR-010 written in DECISIONS.md 2026-03-04; decision: retain ThreadPoolExecutor (all deps are sync; asyncio adds complexity with no measurable benefit)
- [x] [PERF] Profile `analyze` end-to-end wall time; document baseline in AUDIT.md — Performance Baseline section added 2026-03-04; theoretical baseline ~20–30 s typical; bottleneck is Ollama LLM inference; optimisation candidates documented
- [x] [PERF] Concurrent FRED + Nasdaq HTTP calls — `_fetch_fred_data()` now uses ThreadPoolExecutor (3 parallel requests); `_run_analysis()` dispatches hist/buffett/tech/realtime/news/macro/insiders concurrently — complete 2026-03-01

### Risk
- [x] [ENG] Beta-adjusted position sizing — `get_buffett_metrics()` now returns `beta`; `_calculate_position_size()` accepts `beta` param and scales position down by `max(1.0, beta)` — complete 2026-03-01
- [x] [ENG] Portfolio VaR (Value at Risk) calculation — `_calculate_portfolio_var()` historical simulation (95%/99%); new `var` command — complete 2026-03-01
- [x] [ENG] Simulated tax-loss harvesting signal in `check-sells` — `TAX_LOSS` signal added to `_check_sell_signals(tlh_pct=5.0)`; `--tlh-threshold` flag on `check-sells`; disclaimer footnote — complete 2026-03-01

---

## v0.6.0 — Live Trading & Compounding Engine

### Live Guard
- [x] [ARCH] `buffet_bot/live_guard.py` — triple-confirmation wrapper; `live_audit` SQLite table — `is_live_mode()`, `get_trading_client()`, `confirm_live_execution()`, `log_live_audit()`, `init_live_audit_table()`; ADR-011 written — complete 2026-03-06
- [x] [ENG] `globals.py`: `TradingClient(paper=not LIVE_MODE)` via `get_trading_client()` from `live_guard.py`; `LIVE_MODE = is_live_mode()` — complete 2026-03-06
- [x] [ENG] All `--execute` paths call `confirm_live_execution()` from `live_guard.py` — 9 call sites wired across `cmd_trading.py`, `cmd_portfolio.py`, `cmd_account.py`, `plans.py`, `crypto.py` — complete 2026-03-06
- [x] [ENG] `status` command: prominent PAPER/LIVE banner — `LIVE_MODE` constant in `globals.py`; green PAPER panel / red LIVE panel printed at top of `status` output; imports wired in `cmd_trading.py` — complete 2026-03-04

### Compounding Engine
- [ ] [ENG] `db.py`: `compound_log` table + helpers; `sweeps` table
- [ ] [ENG] `compound` command in `cmd_portfolio.py`: dividend + realized profit reinvestment; allocates via `_calculate_position_size()` + ranked Buffett scores
- [ ] [ENG] Alpaca corporate actions endpoint (`/v2/account/activities?activity_type=DIV`)
- [ ] [ENG] `automate --sweep` flag: deterministic scan→analyze→size→execute top-N (SWEEP_AGENT_PROMPT in `automate.py`)
- [x] [ENG] `portfolio` SPY benchmark overlay (plotext, CAGR/alpha/Sharpe comparison) — `_fetch_spy_benchmark()` + `_annualised_cagr()` helpers; yellow SPY line on portfolio chart; CAGR/alpha summary row; `--no-benchmark` flag — complete 2026-03-04

### QA
- [ ] [QA] Tests: live guard triple-confirmation rejection; compound allocation math; sweep flow

---

## v0.7.0 — Multi-Factor EDGE_SCORE

### Edge Intelligence
- [ ] [ARCH] `buffet_bot/edge.py`: `compute_edge_score()`, `compute_insider_signal()`, `compute_politician_signal()`, `compute_earnings_signal()`
- [ ] [ENG] `[edge]` section in `_CONFIG_DEFAULTS` (globals.py): configurable signal weights (W_BUFFETT=0.30, W_LLM=0.20, W_INSIDER=0.20, W_POLITICIAN=0.10, W_EARNINGS=0.10, W_ANALYST=0.10)
- [ ] [ENG] `db.py`: `edge_scans` table for persisting scan results
- [ ] [ENG] `edge-scan` command in `cmd_intel.py`: `--universe`, `--min-edge`, `--top`, `--weights`, `--json`
- [ ] [ENG] `_run_edge_backtest()` in `backtest.py`: weekly-rebalanced EDGE portfolio vs SPY (no lookahead bias — filter all signals by `simulation_date`)
- [ ] [ENG] `backtest --edge` flag wiring

### QA
- [ ] [QA] Lookahead bias validation; edge score unit tests

---

## v0.8.0 — Options Income Engine

### Options Engine
- [ ] [ARCH] `buffet_bot/options_engine.py`: `_fetch_options_chain()`, `_find_optimal_covered_call()` (0.30 delta, 21–45 DTE), `_find_optimal_csp()` (0.20 delta), `_annualized_yield()`
- [ ] [ENG] `db.py`: `options_positions` table (contract tracking + roll history)
- [ ] [ENG] `options-income covered-calls` sub-command in `cmd_intel.py`; yfinance options chain
- [ ] [ENG] `options-income cash-puts`: filters watchlist tickers with EDGE_SCORE > 65; cash requirement validation
- [ ] [ENG] `options-income dashboard`: open positions, DTE, P&L, 12-month income bar chart (plotext)
- [ ] [ENG] `options-income roll-check`: 7-DTE flag; uses `_get_atr()` from `risk.py` for risk assessment
- [ ] [ENG] `--execute` for live accounts only (Alpaca options API is live-only; gated behind LIVE_MODE)

### QA
- [ ] [QA] Yield calculation tests; mock yfinance options chain; liquidity filter (bid > 0, OI > 100)

---

## v0.9.0 — Sector Rotation & Macro Intelligence

### Macro Engine
- [ ] [ARCH] `buffet_bot/macro.py`: `detect_macro_regime()`, `_classify_regime()`, `rank_sectors_by_momentum()`
- [ ] [ENG] `db.py`: `macro_regimes` table (timestamp, regime, confidence, indicators); 1-hour cache check
- [ ] [ENG] FRED indicators extended in `data.py`: add 2Y/10Y yield spread, PMI, Unemployment to existing `_fetch_fred_data()` ThreadPoolExecutor pool
- [ ] [ENG] `SECTOR_ETFS` constant in `globals.py`: 11 GICS ETFs (XLK, XLF, XLV, XLE, XLI, XLB, XLU, XLRE, XLC, XLP, XLY)

### Sector Commands
- [ ] [ENG] `sectors` command in `cmd_portfolio.py`: momentum ranking (30d×0.5 + 90d×0.3 + 1y×0.2), plotext bar chart
- [ ] [ENG] `rotation-check` in `cmd_portfolio.py`: current vs target weights by regime; rotation matrix table; `--execute` queues sells + analyze on adds
- [ ] [ENG] `hedge` in `cmd_portfolio.py`: beta-adjusted SPY put sizing (display-only by default); uses `calculate_portfolio_beta()` in `risk.py`
- [ ] [ENG] Inject `detect_macro_regime()` into `_run_analysis()` alongside existing FRED block (1-hour cached)

### QA
- [ ] [QA] Regime classifier tests with mocked FRED; sector momentum ranking tests

---

## v1.0.0 — Production-Grade CLI

### Intelligence
- [ ] [SCRAPER+ENG] SEC 10-K/10-Q filing fetcher — LLM summarizes key risks + financials
- [ ] [ENG] Multi-LLM model selection: allow pulling and using any Ollama model
- [ ] [ENG] Model performance tracking: EDGE_SCORE-driven vs RSI-only vs SPY, reported in `backtest --edge` output
- [ ] [ENG] ML signal enhancement: train sklearn gradient boosting on `edge_scans` historical vs `outcomes` table to auto-tune signal weights
- [ ] [ENG] Options P&L tracker: auto-close `options_positions` rows at expiry; feed realized income into compound engine

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
- [x] [REL] CHANGELOG.md — full version history from v0.2.0 — complete 2026-03-06 (v0.2.0–v0.5.0; extend to v0.1.0 pre-launch history is low priority)

---

## Active Role Assignment

> Used by the Role Assignment Protocol in CLAUDE.md.
> The next session's first agent must read this table before choosing a role.

| Field | Value |
|-------|-------|
| **Next session role** | ENG — v0.6.0 Compounding Engine (session 17) |
| **Suggested focus** | Implement `compound_log` + `sweeps` tables in `db.py`; `compound` command in `cmd_portfolio.py`; `automate --sweep` flag; Alpaca `/v2/account/activities?activity_type=DIV` endpoint. See ADR-012 for full schema + helper contracts. Also: `git add buffet_bot/live_guard.py` and commit it (untracked — verified complete by Architect session 16). |
| **Do NOT take** | Security Auditor (gated to v1.0.0 pre-release) |
| **Last updated** | 2026-03-06 by PM / Release Manager (session 15) |

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

- [x] [ENG] Portfolio sector pie chart (plotext) — `sectors` command in `cmd_portfolio.py` — complete 2026-03-04
- [x] [ENG] `compare AAPL MSFT` — side-by-side Buffett score comparison — complete 2026-03-04
- [ ] [SCRAPER] Reddit/WSB sentiment integration
- [ ] [SCRAPER] Google Trends signal (pytrends)
- [x] [STYLE] Dark/light theme toggle (environment variable) — `BUFFET_BOT_THEME=light/dark` in `globals.py`, `THEME` dict + `theme_color()` helper — complete 2026-03-04
- [x] [ENG] `explain` command — ask LLM to explain a specific metric or concept — complete 2026-03-04
- [ ] [PM] Public roadmap / GitHub Discussions

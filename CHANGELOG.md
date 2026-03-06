# Changelog

All notable changes to Buffet-Bot are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/)

---

## [Unreleased]

### Added
- `live_guard.py` triple-confirmation wrapper for live trading (in progress — Architect session 16)
- `compound` command: dividend + realized profit reinvestment via Alpaca corporate actions endpoint
- `automate --sweep` flag: deterministic scan → analyze → size → execute pipeline

---

## [0.5.0] — 2026-03-06

This release completes the **Automation + Advanced Signals** milestone. The entire
codebase was refactored from a single 4,500-line `main.py` into 13 focused modules.
Thirty-five-plus commands are now available, backed by 149 passing tests and a
completed security audit.

### Added

**New commands:**
- `compare TICKER_A TICKER_B` — concurrent Buffett metric fetch for two tickers;
  side-by-side Rich table with per-metric PASS/FAIL badges and an edge-winner verdict
- `explain CONCEPT` — LLM-powered Buffett-framed metric explainer; `--model` flag to
  select inference model; gracefully degrades if Ollama is offline
- `sectors` — GICS sector allocation table for all open positions with value, weight,
  and concentration coloring; plotext horizontal bar chart; `--no-chart` flag
- `var` — portfolio Value at Risk via historical simulation at 95 % and 99 % confidence
- `automate` — ReAct agent loop: LLM chains scan/analyze/buy tools autonomously to
  fulfill a natural-language goal; dry-run by default; `--execute` for paper trades;
  `--budget` cap; `--max-steps` limit
- `beats log` / `beats show` — earnings surprise tracker; logs beat/miss history per
  ticker in SQLite; displays beat-rate summary
- `browse` — sector overview, sector filter (`--sector`), keyword company search,
  full EDGAR 10-K+ search (`--all`); backed by 366-company `universe.py` database
- `completion` — shell tab-completion via Click 8 built-in completion mechanism

**New features on existing commands:**
- `scan --notify` / `--min-score` — plain-text cron/email report mode; Rich spinner
  suppressed; BUY CANDIDATES list filtered by `--min-score` (default 60)
- `plans --schedule NAME FREQ` / `--run-due` — run-plan scheduler; daily/weekly/
  biweekly/monthly/off frequencies; Schedule + Last Run columns in plan list
- `alerts check` — evaluate all set price/RSI alerts and report status
- `check-sells --tlh-threshold` — simulated tax-loss harvesting signal (`TAX_LOSS`)
  added to `_check_sell_signals()`; percentage threshold configurable
- `analyze` — analyst consensus block (rating, target price, upside %, analyst count,
  upgrades/downgrades) injected into LLM prompt and printed in output panel
- `analyze` — multi-timeframe signals (daily RSI-14, weekly RSI-14, monthly SMA trend,
  50-day SMA position) wired into concurrent fetch and LLM prompt
- `portfolio --no-benchmark` — SPY benchmark overlay with yellow plotext line; CAGR
  and alpha summary row; `_fetch_spy_benchmark()` and `_annualised_cagr()` helpers
- `status` — prominent PAPER / LIVE banner at top of output; green panel in paper
  mode, red panel in live mode; driven by `LIVE_MODE` constant in `globals.py`

**Infrastructure:**
- `BUFFET_BOT_THEME` environment variable — `dark` (default) / `light` palette;
  `_THEMES` dict, `THEME` dict, and `theme_color(role)` helper in `globals.py`
- `LIVE_MODE = False` stub in `globals.py` — wired for future `live_guard.py`
- Concurrent FRED + Nasdaq HTTP calls — `_fetch_fred_data()` uses `ThreadPoolExecutor`
  (3 parallel requests); `_run_analysis()` dispatches 9 workers concurrently
- Beta-adjusted position sizing — `get_buffett_metrics()` returns `beta`;
  `_calculate_position_size()` scales position down by `max(1.0, beta)`
- `buffet_bot/universe.py` — 366-company database across 11 GICS sectors;
  `list_companies()`, `search_companies()`, `search_edgar()`
- `buffet_bot/automate.py` — ReAct agent loop implementation
- `buffet_bot/insiders.py` — SEC EDGAR Form 4 insider transaction fetcher

**Testing:**
- Full pytest suite: 149 passing tests across 5 test files
  (`test_analysis.py`, `test_db.py`, `test_data_fetching.py`, `test_cli.py`,
  `test_security.py`)

**Documentation:**
- `agents/DECISIONS.md` ADR-010 — ThreadPoolExecutor retained over asyncio;
  5 justification points documented; all deps are synchronous
- `agents/AUDIT.md` Performance Baseline — theoretical wall-time breakdown per
  concurrent worker; primary bottleneck is Ollama LLM inference (~75–85 % of total
  time); optimisation candidates listed; live profiling instructions included
- `PITCH.md` — command count updated to 35+; v0.6–v0.9 feature sections added;
  "The 10X Compounding Framework" section; Roadmap Preview table

### Changed

- **Modular refactor** — `buffet_bot/main.py` split from 4,532 lines into 13 modules:
  `globals.py`, `db.py`, `data.py`, `display.py`, `analysis.py`, `backtest.py`,
  `risk.py`, `projections.py`, `plans.py`, `cmd_trading.py`, `cmd_intel.py`,
  `cmd_portfolio.py`, `cmd_account.py`; slim `main.py` (92 lines) now only registers
  commands via `cli.add_command`
- `_run_analysis()` concurrent fan-out expanded from 7 → 9 `ThreadPoolExecutor`
  workers to accommodate analyst consensus and multi-timeframe signal fetches
- `_COMMON_TICKERS` expanded from 45 → 366 tickers sourced from `_COMPANY_DB`;
  tab-completion now covers all 11 GICS sectors
- Vibrant UI overhaul across all command modules — consistent color palettes,
  Rich panels, progress spinners, and table formatting

### Fixed

- `_safe_plan_path()` allowlist validation added (`[a-zA-Z0-9_-]` characters only)
  as a second layer of path traversal defence on top of the existing pathlib check;
  fixes `test_dotdot_only_blocked` failure (`'..'` + `'.json'` = `'...json'` bypass)
- yfinance crumb errors and SQLite DB lock errors suppressed with graceful degradation
- Ollama pre-check added before LLM inference — prints actionable error if Ollama is
  not running rather than crashing with an unhelpful exception
- Removed dead `asyncio` import (AUDIT.md D-001) from `main.py`
- `_analyze_crypto()` migrated from `main.py` to `crypto.py` (ADR-008)

### Security

- FINDING-001 (path traversal in plan management) — **fixed** — `_safe_plan_path()`
  now applies allowlist validation before the pathlib containment check; see
  `agents/SECURITY-AUDIT.md`
- FINDING-002 (XML entity expansion in SEC EDGAR XML parse) — **informational** —
  `defusedxml` not adopted; EDGAR XML is trusted-source; documented in SECURITY-AUDIT.md
- Full credential handling audit complete — no hardcoded keys, no console leakage,
  `.gitignore` covers `.env` and all key files
- SQL injection audit complete — all DB operations use parameterized queries
- Data exfiltration audit complete — no cloud LLM calls in any code path; all
  inference is local via Ollama

---

## [0.4.1] — 2026-03-01

### Added

- `options` command — put/call ratio display, unusual volume flag
- `rebalance` command — compare actual portfolio allocation vs target; suggest trades
- `watchlist` subgroup — `watchlist add TICKER`, `watchlist remove TICKER`,
  `watchlist show`
- `alerts` command — set price/RSI thresholds; `alerts check` evaluates all alerts
- `insiders` command — SEC EDGAR Form 4 insider transaction fetcher and display
- `browse` command — 366-company universe browser; sector filter; keyword search;
  full EDGAR 10-K+ (`--all`)
- `completion` command — shell tab-completion via Click 8
- `dashboard` command — multi-ticker live table; column alignment, color thresholds,
  visual polish
- Config file support — `~/.buffet-bot-config.toml` via `config show` / `config init`
  (ADR-009)
- FRED macro indicators injected into LLM analysis prompt
- Earnings calendar integration — upcoming earnings warning in `analyze` output
- `_COMMON_TICKERS` expanded to 366 tickers for tab-completion

### Changed

- `scan` now uses `ThreadPoolExecutor` concurrent fetch instead of serial
  `time.sleep(1)` loop
- Buffett score color-coded: green ≥ 70, yellow ≥ 40, red < 40
- `analyze` output: data source and timestamp footer added; `--json` flag for
  scripting output
- `scan` output: sorted by score, color-coded rows, compact layout; `--json` flag

### Fixed

- Removed dead `asyncio` import (AUDIT.md D-001)
- Migrated `_analyze_crypto()` from `main.py` to `crypto.py` (ADR-008; −131 lines)
- Fixed stale `pyproject.toml` version (`0.2.0` → `0.4.1`)

### Security

- Structural audit of `main.py` — `agents/AUDIT.md` written; split candidates
  documented
- `_safe_plan_path()` path traversal guard added to plan management

---

## [0.4.0] — 2026-02-01

### Added

- `crypto` command — live dashboard for all 8 crypto pairs, or full LLM analysis +
  optional order; `analyze` auto-detects crypto symbols (BTC/USD etc.)
- `insiders` / `news` command — Alpaca headlines + congressional trades + short
  interest + AI summary; `buffet_bot/politicians.py` (House Stock Watcher S3 + FMP)
- `volatile` command — 75-ticker universe, 0–100 volatility score (beta / mktcap /
  short % / 30-day vol); concurrent `ThreadPoolExecutor` scan; `--n` and `--universe`
  flags
- `status` — 3-panel display: Alpaca paper + Coinbase + IBKR (all gracefully degrade
  if unconfigured)
- `buffet_bot/ibkr.py` — synchronous IBKR EWrapper/EClient wrapper; account summary
  + orders

### Changed

- Module split executed: `politicians.py`, `crypto.py`, `volatile.py`, `ibkr.py`
  extracted from `main.py`
- `scan` replaced serial loop with `ThreadPoolExecutor`
- `asyncio` + `concurrent.futures` imported for future async capability

---

## [0.3.0] — 2026-01-15

### Added

- `backtest` command — RSI strategy vs SPY benchmark; Sharpe ratio, max drawdown,
  win rate, profit factor, equity curve chart (plotext)
- `correlate` command — pairwise correlation matrix of holdings; diversity score
- `check-sells` command — automated sell signal audit; STOP / THESIS_BROKEN /
  UNDERPERFORMER / OVERBOUGHT signals; `--execute` flag
- `forecast` command — Monte Carlo simulation; 1,000 paths; P10/median/P90 cone
- `whatif` command — interactive what-if calculator
- `scenarios` command — 5-scenario comparison table (Conservative / AI Balanced /
  Aggressive / Bear / S&P 500)
- `milestones` command — years to $25k, $50k, $100k, $250k, $500k, $1M
- `stream` command — rolling 60-tick terminal price chart; 1/5/15-minute refresh
- `chart` command — terminal SMA overlay + mplfinance candlestick PNG export
- `dashboard` command — multi-ticker live table refreshing every 60 seconds
- SQLite persistence — `~/.buffet-bot.db` logs every BUY recommendation and outcome
- `plans` command — save, list, run, delete multi-stock investment plans with budgets
- `ask` command — one-shot investing question to both LLMs
- `chat` command — multi-turn REPL conversation with model selection
- `lookup` command — find ticker by company name via yfinance
- `guide` command — step-by-step beginner wizard: analyze → plan → execute

### Changed

- ATR-based dynamic position sizing replaces flat LLM-suggested quantity on every BUY
- `analyze` decision flow expanded: optional RSI/MACD technicals when `--risk high`

---

## [0.2.0] — 2026-01-01

### Added

- `analyze` command — 6-month price history, Buffett fundamentals (ROE, debt/equity,
  operating margin), dual-LLM consensus vote, optional paper trade execution
- `buy` command — analyze then immediately prompt to buy
- `scan` command — score a fixed watchlist on Buffett criteria
- `status` command — account cash and buying power
- `history` command — past paper orders with fill prices
- `portfolio` command — equity curve chart over time
- Alpaca paper trading integration (`paper=True` hardcoded)
- Two local LLM models via Ollama: `deepseek-r1` (primary), `qwen2.5:7b` (consensus)
- Buffett scoring — 100-point score: ROE > 15 % (+40), Debt/Equity < 50 (+30),
  Operating Margin > 10 % (+30)

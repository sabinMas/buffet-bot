# Buffet-Bot Roadmap

> Managed by: Product Manager Agent
> Last updated: 2026-02-28
> Current version: v0.4.0

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

## v0.4.1 — Data Expansion + UX Polish (Next Up)

### Intelligence
- [ ] [SCRAPER+ENG] SEC EDGAR Form 4 insider transaction fetcher + `insiders` command
- [~] [ENG] FRED macro indicators injected into LLM prompt — TICKET-001 assigned, spec in INTEGRATION-TICKETS.md
- [~] [ENG] Earnings calendar integration — TICKET-002 assigned, spec in INTEGRATION-TICKETS.md
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
- [ ] [ENG] Shell autocomplete via `click-completion` or `argcomplete`
- [~] [STYLE] `dashboard` command — column alignment, color thresholds, visual polish

### Architecture
- [x] [ARCH] Structural audit of main.py — `agents/AUDIT.md` written; split candidates documented; SCHEMA.md + PATTERNS.md updated
- [x] [ENG] Config file: `~/.buffet-bot-config.toml` — ADR-009 implemented; `config show` / `config init` commands added
- [x] [ENG] Remove dead `asyncio` import (line 3, main.py) — flagged in AUDIT.md D-001
- [x] [ENG] Migrate `_analyze_crypto()` from `main.py` to `crypto.py` — ADR-008 implemented; main.py -131 lines

---

## v0.5.0 — Automation + Advanced Signals

### Automation
- [ ] [ENG] Cron-compatible `scan --notify` mode: output parseable for scripts/email
- [ ] [ENG] `run-plan` scheduler: execute saved plans on a schedule
- [ ] [ENG] `alerts check` command: evaluate all set alerts and report

### Signals
- [ ] [SCRAPER+ENG] Analyst consensus ratings (Nasdaq API or similar)
- [ ] [ENG] Multi-timeframe analysis: 1d, 1w, 1mo signals combined
- [ ] [ENG] Earnings surprise tracker: log beat/miss history in SQLite

### Performance
- [ ] [PERF] Resolve async LLM query open question — ThreadPoolExecutor vs asyncio ADR
- [ ] [PERF] Profile `analyze` end-to-end wall time; document baseline in AUDIT.md
- [ ] [PERF] Concurrent FRED + Nasdaq HTTP calls (when TICKET-001/002 implemented)

### Risk
- [ ] [ENG] Beta-adjusted position sizing (replace or augment ATR Kelly)
- [ ] [ENG] Portfolio VaR (Value at Risk) calculation — add to `portfolio` output
- [ ] [ENG] Simulated tax-loss harvesting signal in `check-sells`

---

## v1.0.0 — Production-Grade CLI

### Intelligence
- [ ] [SCRAPER+ENG] SEC 10-K/10-Q filing fetcher — LLM summarizes key risks + financials
- [ ] [ENG] Multi-LLM model selection: allow pulling and using any Ollama model
- [ ] [ENG] Model performance tracking: log LLM recommendation outcomes in DB

### Architecture
- [ ] [ARCH+ENG+QA] Full test suite with `pytest` — mock Alpaca and yfinance responses
- [ ] [ARCH] DB migration system: versioned schema changes

### Security
- [ ] [SEC] Full credential handling audit — .gitignore, no hardcoded keys, no console leakage
- [ ] [SEC+QA] SQL injection audit + test coverage for all DB operations
- [ ] [SEC] Input validation audit — ticker path traversal, model name injection
- [ ] [SEC] Dependency CVE scan (`pip-audit`) — fix any CVSS ≥7.0 before v1.0.0 release
- [ ] [SEC] Data exfiltration audit — verify no cloud LLM calls exist in any code path

### Distribution
- [ ] [PM+ENG+REL] PyPI package: `pip install buffet-bot`
- [ ] [REL] Docker image with Ollama sidecar
- [ ] [PM+REL] Contribution guide and PR template
- [ ] [REL] CHANGELOG.md — full version history from v0.1.0

---

## Backlog (Unscheduled)

- [ ] [ENG] Portfolio sector pie chart (plotext)
- [ ] [ENG] `compare AAPL MSFT` — side-by-side Buffett score comparison
- [ ] [SCRAPER] Reddit/WSB sentiment integration
- [ ] [SCRAPER] Google Trends signal (pytrends)
- [ ] [STYLE] Dark/light theme toggle (environment variable)
- [ ] [ENG] `explain` command — ask LLM to explain a specific metric or concept
- [ ] [PM] Public roadmap / GitHub Discussions

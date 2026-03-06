# Architectural Decision Log — Buffet-Bot

> Owned by: Architect Agent + Product Manager Agent
> Purpose: Record major decisions with their rationale so future agents don't re-litigate them.
> Format: Most recent decisions at the top.

---

## How to Add an Entry

```markdown
## ADR-NNN: Short decision title
- **Date:** YYYY-MM-DD
- **Status:** Accepted | Superseded by ADR-NNN | Deprecated
- **Decided by:** [agent role or user]

**Context:** What was the situation that forced a decision?
**Decision:** What was chosen?
**Rationale:** Why this over the alternatives?
**Consequences:** What does this enable or constrain going forward?
```

---

## ADR-015: Macro Regime Engine — `buffet_bot/macro.py`
- **Date:** 2026-03-06
- **Status:** Accepted
- **Decided by:** Architect Agent (session 16)

**Context:**

v0.9.0 introduces sector rotation driven by macroeconomic regime detection. `data.py` already fetches three FRED indicators (`_fetch_fred_data()`: 10Y yield, CPI, Fed Funds Rate) inside `_run_analysis()`'s concurrent fan-out. The v0.9.0 roadmap adds regime classification, sector momentum ranking, and a `rotation-check` command. This requires a dedicated module that centralizes macro state logic — keeping it out of `data.py` (which is a pure fetcher) and `analysis.py` (which is per-ticker).

**Decision:**

Create `buffet_bot/macro.py` with:

### Module Interface

```python
# buffet_bot/macro.py

REGIMES = ('expansion', 'peak', 'contraction', 'trough')

def detect_macro_regime(use_cache: bool = True) -> dict:
    """Classify the current macro regime using FRED indicators.

    Fetches (or loads from 1-hour cache in macro_regimes table):
      - 10Y Treasury yield (DGS10)
      - 2Y Treasury yield (DGS2) — NEW: yield curve spread
      - CPI YoY (CPIAUCSL)
      - Federal Funds Rate (FEDFUNDS)
      - Unemployment Rate (UNRATE) — NEW
      - ISM PMI proxy (MANEMP as manufacturing employment proxy)

    Returns:
        {
          "regime": one of REGIMES,
          "confidence": 0.0–1.0,
          "indicators": {name: value, ...},
          "cached": bool,
          "timestamp": ISO UTC string,
        }
    """

def _classify_regime(indicators: dict) -> tuple[str, float]:
    """Deterministic regime classifier from indicator dict.

    Rules (all simple threshold logic — no ML, no external deps):
      - expansion:   yield_spread >= 0.5 AND unemployment <= 5.0 AND cpi <= 4.0
      - peak:        cpi > 4.0 AND fed_funds rising AND unemployment <= 5.0
      - contraction: yield_spread < 0 (inverted curve) OR unemployment > 6.0
      - trough:      yield_spread recovering (>= -0.5) AND cpi falling AND unemployment > 5.5

    Returns: (regime_name, confidence_score)
    """

def rank_sectors_by_momentum(
    period_weights: tuple[float, float, float] = (0.5, 0.3, 0.2)
) -> list[dict]:
    """Rank 11 GICS sector ETFs by weighted momentum score.

    Fetches 1-year price history for SECTOR_ETFS (from globals.py) via yfinance.
    Computes: 30d_return * w[0] + 90d_return * w[1] + 252d_return * w[2]

    Returns list of dicts sorted descending by score:
        [{"etf": "XLK", "sector": "Technology", "score": 0.23, ...}, ...]
    """

def get_regime_sector_weights(regime: str) -> dict[str, float]:
    """Return target allocation weights by sector ETF for a given regime.

    Regime → overweight / underweight sectors (standard rotation playbook):
      expansion:   XLK, XLY, XLF overweight; XLU, XLP, XLRE underweight
      peak:        XLE, XLB, XLI overweight; XLK, XLRE underweight
      contraction: XLU, XLP, XLV overweight; XLF, XLY, XLK underweight
      trough:      XLF, XLY, XLK overweight; XLE, XLB underweight

    Returns: {etf_symbol: target_weight_pct, ...}  (weights sum to 1.0)
    """
```

### DB Schema: `macro_regimes` Table (cache)

```sql
CREATE TABLE IF NOT EXISTS macro_regimes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,       -- ISO 8601 UTC
    regime      TEXT    NOT NULL,       -- expansion | peak | contraction | trough
    confidence  REAL    NOT NULL,       -- 0.0 – 1.0
    indicators  TEXT    NOT NULL,       -- JSON blob of raw FRED values
    ttl_seconds INTEGER NOT NULL DEFAULT 3600  -- 1-hour cache
);
CREATE INDEX IF NOT EXISTS idx_macro_regimes_timestamp ON macro_regimes(timestamp);
```

Cache logic: `detect_macro_regime()` checks if the most recent row is < `ttl_seconds` old; if so, returns it (with `"cached": True`). Otherwise fetches live data, inserts a new row, and returns it.

### Integration Points

1. **`globals.py`**: Add `SECTOR_ETFS` constant (11-item dict: ETF → sector name).
2. **`db.py`**: Add `init_macro_table()` called from `init_db()`.
3. **`data.py`**: Extend `_fetch_fred_data()` to also fetch DGS2, UNRATE (add to the existing ThreadPoolExecutor pool — now 5 parallel requests, was 3).
4. **`analysis.py`**: Inject `detect_macro_regime()` result into `_run_analysis()` concurrent fan-out (slot 10, `max_workers=10`); add `macro_block` to LLM prompt after existing `fred_block`.
5. **`cmd_portfolio.py`**: `sectors` command updated to use `rank_sectors_by_momentum()`; new `rotation-check` and `hedge` commands added.

**Rationale:**
- Regime classification is deterministic threshold logic, not ML — no training data, no sklearn dependency, no risk of stale model.
- 1-hour cache prevents FRED API hammering on repeated `analyze` calls.
- Sector weights follow standard CFA/Vanguard sector rotation research — simple, documented, explainable to users.

**Consequences:**
- `detect_macro_regime()` adds ~0.3s to `analyze` when cache is cold (one FRED batch request).
- FRED_API_KEY remains optional; `_fetch_fred_data()` already degrades gracefully without it.
- New FRED series (DGS2, UNRATE) are public and free-tier accessible.

---

## ADR-014: Options Engine — `buffet_bot/options_engine.py`
- **Date:** 2026-03-06
- **Status:** Accepted
- **Decided by:** Architect Agent (session 16)

**Context:**

v0.8.0 adds income-generating options strategies (covered calls, cash-secured puts). The existing `options` command in `cmd_intel.py` is display-only (put/call ratio, unusual volume). An `options_engine.py` module is needed to handle contract selection logic, yield calculations, and position tracking. Note: Alpaca's options API requires a live account (paper options trading is not supported) — all `--execute` paths are therefore gated behind `LIVE_MODE`.

**Decision:**

Create `buffet_bot/options_engine.py` with:

### Module Interface

```python
# buffet_bot/options_engine.py

def fetch_options_chain(ticker: str) -> dict:
    """Fetch the full options chain for a ticker via yfinance.

    Returns:
        {
          "ticker": str,
          "expiries": [date_str, ...],  -- sorted ascending
          "calls": pd.DataFrame,        -- yfinance calls table
          "puts": pd.DataFrame,         -- yfinance puts table
        }
    Raises ValueError if no options data available.
    """

def find_optimal_covered_call(
    ticker: str,
    current_price: float,
    target_delta: float = 0.30,
    min_dte: int = 21,
    max_dte: int = 45,
) -> dict | None:
    """Find the best covered call contract near target_delta and DTE range.

    Delta proxy (greeks-free): strike / current_price ratio.
      delta ≈ 0.30 → strike ≈ current_price * 1.03 to 1.07 (OTM)

    Returns contract dict or None if no suitable contract found.
    """

def find_optimal_csp(
    ticker: str,
    current_price: float,
    target_delta: float = 0.20,
    min_dte: int = 21,
    max_dte: int = 45,
    max_cash_required: float = 10_000.0,
) -> dict | None:
    """Find the best cash-secured put near target_delta.

    Delta proxy: strike / current_price ≈ 0.92 to 0.96 for delta 0.20.
    Filters: bid > 0 (liquid), open_interest > 100 (active).

    Returns contract dict or None.
    """

def annualized_yield(premium: float, strike: float, dte: int) -> float:
    """Calculate annualized premium yield.

    Formula: (premium / strike) * (365 / dte)
    Returns float (e.g., 0.18 = 18% annualized).
    """
```

### DB Schema: `options_positions` Table

```sql
CREATE TABLE IF NOT EXISTS options_positions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at      TEXT    NOT NULL,   -- ISO 8601 UTC
    ticker         TEXT    NOT NULL,
    strategy       TEXT    NOT NULL,   -- 'COVERED_CALL' | 'CASH_PUT'
    contract_sym   TEXT    NOT NULL,   -- OCC symbol (e.g., AAPL251219C00200000)
    expiry         TEXT    NOT NULL,   -- YYYY-MM-DD
    strike         REAL    NOT NULL,
    premium_credit REAL    NOT NULL,   -- per share (×100 for total)
    contracts      INTEGER NOT NULL DEFAULT 1,
    status         TEXT    NOT NULL DEFAULT 'OPEN',  -- OPEN | EXPIRED | ROLLED | CLOSED
    closed_at      TEXT    NOT NULL DEFAULT '',
    realized_pnl   REAL    NOT NULL DEFAULT 0.0,
    roll_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_options_pos_ticker  ON options_positions(ticker);
CREATE INDEX IF NOT EXISTS idx_options_pos_expiry  ON options_positions(expiry);
CREATE INDEX IF NOT EXISTS idx_options_pos_status  ON options_positions(status);
```

### Key Design Constraints

1. **No live greeks**: yfinance provides `delta` in some chains but not reliably. Use strike/price ratio as a delta proxy — well-documented approximation, zero external API cost.
2. **Live-only execution**: All `--execute` paths are gated with `if not LIVE_MODE: raise click.ClickException("Options execution requires LIVE mode")`. This is the only command family where live mode is a prerequisite (not just optional).
3. **`roll-check` at 7 DTE**: `options-income roll-check` flags positions within 7 days of expiry. Uses `_get_atr()` from `risk.py` to size the roll strikes.
4. **`edge_score` integration (v0.8+)**: `options-income cash-puts` filters for tickers with `EDGE_SCORE > 65` — requires `edge.py` to be implemented first.

**Consequences:**
- `options_engine.py` has a hard dependency on `edge.py` for CSP ticker filtering in v0.8.0. ENG must implement in order.
- No Alpaca paper options support means QA tests for execution paths must mock the Alpaca client.

---

## ADR-013: Multi-Factor Edge Score — `buffet_bot/edge.py`
- **Date:** 2026-03-06
- **Status:** Accepted
- **Decided by:** Architect Agent (session 16)

**Context:**

v0.7.0 replaces the simple Buffett score as the primary ranking signal with a composite `EDGE_SCORE` combining five independent signal families. The score drives `edge-scan`, `backtest --edge`, and (in v0.8.0) CSP ticker filtering in `options_engine.py`. A dedicated module is needed to compute and persist these scores without polluting the existing `analysis.py` flow.

**Decision:**

Create `buffet_bot/edge.py` with:

### Module Interface

```python
# buffet_bot/edge.py

# Default signal weights — overridable via config [edge] section in globals.py
DEFAULT_WEIGHTS = {
    'buffett':   0.30,  # Buffett score (ROE, debt, margin) — from data.py
    'llm':       0.20,  # LLM consensus confidence — from analysis.py
    'insider':   0.20,  # Insider buy/sell signal — from db.py earnings_surprises + insiders
    'politician':0.10,  # Congressional trade signal — from politicians.py
    'earnings':  0.10,  # Earnings surprise streak — from db.py earnings_surprises
    'analyst':   0.10,  # Analyst consensus upside — from data.py get_analyst_consensus
}

def compute_edge_score(
    ticker: str,
    weights: dict | None = None,
    simulation_date: str | None = None,  # for backtesting — filter signals by date
) -> dict:
    """Compute the composite EDGE_SCORE for a ticker.

    Returns:
        {
          "ticker": str,
          "edge_score": 0–100,
          "components": {signal_name: {"raw": float, "weighted": float}, ...},
          "weights_used": dict,
          "simulation_date": str | None,
        }
    """

def compute_insider_signal(ticker: str, lookback_days: int = 90,
                           simulation_date: str | None = None) -> float:
    """Return 0–100 signal from recent insider Form 4 filings.

    +points for buys, -points for sells, scaled by transaction count.
    Reads from insiders module (SEC EDGAR Form 4).
    """

def compute_politician_signal(ticker: str, lookback_days: int = 180,
                              simulation_date: str | None = None) -> float:
    """Return 0–100 signal from congressional trading activity.

    Weights buy transactions more heavily than sells (politicians rarely sell
    stocks they just disclosed buying).
    Reads from politicians module (House Stock Watcher).
    """

def compute_earnings_signal(ticker: str, lookback_quarters: int = 4) -> float:
    """Return 0–100 signal from earnings surprise history in SQLite.

    Perfect 4-quarter beat streak = 100. Mix of beats/misses scales down.
    Reads from db.get_earnings_history().
    """
```

### DB Schema: `edge_scans` Table

```sql
CREATE TABLE IF NOT EXISTS edge_scans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at      TEXT    NOT NULL,   -- ISO 8601 UTC
    ticker          TEXT    NOT NULL,
    edge_score      REAL    NOT NULL,
    buffett_score   REAL    NOT NULL DEFAULT 0.0,
    llm_score       REAL    NOT NULL DEFAULT 0.0,
    insider_score   REAL    NOT NULL DEFAULT 0.0,
    politician_score REAL   NOT NULL DEFAULT 0.0,
    earnings_score  REAL    NOT NULL DEFAULT 0.0,
    analyst_score   REAL    NOT NULL DEFAULT 0.0,
    weights_json    TEXT    NOT NULL DEFAULT '{}',
    simulation_date TEXT    NOT NULL DEFAULT ''   -- empty = live scan
);
CREATE INDEX IF NOT EXISTS idx_edge_scans_ticker     ON edge_scans(ticker);
CREATE INDEX IF NOT EXISTS idx_edge_scans_scanned_at ON edge_scans(scanned_at);
```

### Config Extension (`globals.py` `_CONFIG_DEFAULTS`)

```python
_CONFIG_DEFAULTS = {
    ...existing sections...,
    'edge': {
        'w_buffett':    0.30,
        'w_llm':        0.20,
        'w_insider':    0.20,
        'w_politician': 0.10,
        'w_earnings':   0.10,
        'w_analyst':    0.10,
    },
}
```

### Backtest Anti-Lookahead Requirement

`compute_edge_score(simulation_date=DATE)` MUST filter all time-series signals to only use data available on or before `simulation_date`. This is enforced by:
- `compute_insider_signal`: filters Form 4 filings by `transaction_date <= simulation_date`
- `compute_politician_signal`: filters congressional trades by `transaction_date <= simulation_date`
- `compute_earnings_signal`: filters `earnings_surprises` by `report_date <= simulation_date`

LLM and Buffett signals are excluded from lookahead-biased backtest runs (no historical LLM outputs stored). The `backtest --edge` flag runs weekly rebalancing using only non-LLM signals when `simulation_date` is provided.

**Consequences:**
- `edge.py` requires `insiders.py`, `politicians.py`, and `db.py` — no new external dependencies.
- `options_engine.py` CSP filtering depends on `edge.py` being implemented first.
- Signal weights in TOML config allow users to tune the model without code changes.

---

## ADR-012: Compounding Engine — `compound_log` + `sweeps` tables
- **Date:** 2026-03-06
- **Status:** Accepted
- **Decided by:** Architect Agent (session 16)

**Context:**

v0.6.0 introduces a `compound` command that reinvests dividends and realized profits by allocating them via `_calculate_position_size()` + Buffett-ranked tickers. Two new capabilities need SQLite persistence: (1) a log of every reinvestment event for audit/reporting, and (2) a record of `automate --sweep` runs for deterministic scan→analyze→size→execute flows. Neither of these belongs in the existing `recommendations` table (which tracks LLM analysis outputs, not execution events).

**Decision:**

Add two tables to `db.py` and the corresponding helpers:

### DB Schema: `compound_log` Table

```sql
CREATE TABLE IF NOT EXISTS compound_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,   -- ISO 8601 UTC
    source          TEXT    NOT NULL,   -- 'DIVIDEND' | 'REALIZED_PROFIT' | 'MANUAL'
    ticker          TEXT    NOT NULL,   -- source ticker (e.g. AAPL paid dividend)
    amount_usd      REAL    NOT NULL,   -- gross amount available for reinvestment
    allocated_to    TEXT    NOT NULL,   -- JSON array of {ticker, qty, price} dicts
    total_deployed  REAL    NOT NULL DEFAULT 0.0,
    undeployed      REAL    NOT NULL DEFAULT 0.0,
    notes           TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_compound_log_timestamp ON compound_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_compound_log_source    ON compound_log(source);
```

### DB Schema: `sweeps` Table

```sql
CREATE TABLE IF NOT EXISTS sweeps (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT    NOT NULL,   -- ISO 8601 UTC
    completed_at  TEXT    NOT NULL DEFAULT '',
    goal          TEXT    NOT NULL,   -- the natural-language goal passed to automate --sweep
    budget_usd    REAL    NOT NULL,
    tickers_scanned INTEGER NOT NULL DEFAULT 0,
    orders_placed   INTEGER NOT NULL DEFAULT 0,
    total_deployed  REAL    NOT NULL DEFAULT 0.0,
    summary       TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'RUNNING'  -- RUNNING | COMPLETE | FAILED
);
```

### Helper Function Contracts (for ENG reference)

```python
# db.py additions

def log_compound_event(
    source: str,           # 'DIVIDEND' | 'REALIZED_PROFIT' | 'MANUAL'
    ticker: str,           # source ticker
    amount_usd: float,     # gross available
    allocated_to: list,    # [{ticker, qty, price}, ...]
) -> int:
    """Insert a compound_log row. Returns the new row id."""

def get_compound_history(days: int = 90) -> list[dict]:
    """Return compound_log rows within the last N days, newest first."""

def create_sweep(goal: str, budget_usd: float) -> int:
    """Insert a sweeps row with status=RUNNING. Returns the new row id."""

def complete_sweep(sweep_id: int, tickers_scanned: int,
                   orders_placed: int, total_deployed: float,
                   summary: str, status: str = 'COMPLETE') -> None:
    """Update a sweeps row to COMPLETE or FAILED."""

def get_sweep_history(limit: int = 20) -> list[dict]:
    """Return recent sweep rows, newest first."""
```

### `compound` Command Design (for ENG reference)

```
buffet-bot compound [--source dividends|profits|all] [--budget FLOAT] [--execute]
```

Flow:
1. Fetch Alpaca corporate action activities (`/v2/account/activities?activity_type=DIV`)
2. Fetch recent realized P&L from `outcomes` table (or Alpaca activities `activity_type=PTC`)
3. Sum compoundable income by source filter
4. Run `scan` on the universe, rank by Buffett score
5. Allocate via `_calculate_position_size()` for each top-ranked ticker until budget exhausted
6. Display allocation table; if `--execute`, place orders via `buy` + `confirm_live_execution()`
7. Log to `compound_log` via `log_compound_event()`

### `automate --sweep` Design (for ENG reference)

The `--sweep` flag adds a `SWEEP_AGENT_PROMPT` template to `automate.py`. Sweep differs from standard automate:
- **Deterministic**: scan → rank → size → execute top-N (no open-ended ReAct loop)
- **Budget cap**: respects `--budget` strictly; aborts if no tickers pass Buffett threshold
- **Logged**: creates a `sweeps` row via `create_sweep()`, updates on completion

**Rationale:**
- Separate tables from `recommendations` — compound events are execution records, not analysis outputs.
- `compound_log.allocated_to` is JSON (not normalized rows) to keep the insert atomic and simple.
- `sweeps` table enables a future `automate history` subcommand showing sweep effectiveness over time.

**Consequences:**
- ENG must add `init_compound_tables()` to `db.py` called from `init_db()`.
- `compound` command has a soft dependency on Alpaca dividend activities endpoint — must degrade gracefully if the endpoint returns empty (new paper accounts have no dividend history).
- `automate --sweep` is the only automate path that creates a `sweeps` row; standard `automate` does not.

---

## ADR-011: Live Trading Guard — `buffet_bot/live_guard.py`
- **Date:** 2026-03-04
- **Status:** Accepted
- **Decided by:** Architect Agent (session 16, pulled forward)

**Context:**

Buffet-Bot was designed as a paper-only trading system (ADR-004: `paper=True` hardcoded, non-negotiable). The v0.6.0 roadmap introduces the ability to optionally enable live trading for users who explicitly opt in. This creates a tension: the system must remain paper-by-default and require extraordinary, deliberate effort to switch to live mode. A single env var or config toggle is insufficient — accidental activation of live mode could result in real financial loss.

There are currently **9 distinct call sites** across 6 modules that place orders via Alpaca's `TradingClient` or construct `TradingClient` instances directly. Two locations hardcode `paper=True` (`globals.py:45`, `crypto.py:354`). Every `--execute` path uses a simple `click.confirm()` prompt that mentions "(Paper)" in its text. None of these paths are aware of trading mode.

The `LIVE_MODE = False` stub already exists in `globals.py:117` but is not wired to anything.

**Problem statement:**

We need a module that:
1. Controls the single source of truth for whether the system is in PAPER or LIVE mode
2. Provides a triple-confirmation gate that must be passed before any live order can be submitted
3. Logs every live trade attempt (successful or rejected) to an audit table for forensic review
4. Integrates cleanly with all 9 existing order-placement call sites without requiring each command to re-implement safety logic
5. Preserves ADR-004's guarantee: paper mode is the default, always, with zero configuration

**Decision:**

Create `buffet_bot/live_guard.py` with the following design:

### Activation Model (Three Independent Factors)

Live mode requires ALL THREE of the following to be true simultaneously. If any single factor is absent, the system remains in paper mode silently:

| Factor | Mechanism | Purpose |
|--------|-----------|---------|
| **Factor 1: Environment variable** | `BUFFET_BOT_LIVE=1` in `.env` or shell | Prevents accidental activation — user must deliberately set this |
| **Factor 2: Secret confirmation token** | `BUFFET_BOT_LIVE_SECRET=<user-chosen-passphrase>` in `.env` | Prevents copy-paste accidents — a second, distinct credential must exist |
| **Factor 3: Runtime confirmation** | Interactive `click.confirm()` prompt with explicit "I understand this uses REAL MONEY" text | Prevents scripted/automated activation without human presence |

The module exports a single boolean `is_live_mode()` that checks Factors 1 and 2. Factor 3 is enforced at each order-placement call site via `confirm_live_execution()`.

### Module Interface

```python
# buffet_bot/live_guard.py

def is_live_mode() -> bool:
    """Check whether LIVE mode is activated (Factors 1 + 2).

    Returns True only when BOTH:
      - BUFFET_BOT_LIVE=1
      - BUFFET_BOT_LIVE_SECRET is set and non-empty

    Returns False in all other cases (the safe default).
    """

def get_trading_client() -> TradingClient:
    """Return an Alpaca TradingClient with paper= derived from is_live_mode().

    This replaces the direct TradingClient construction in globals.py.
    The returned client is a module-level singleton, created once.
    """

def confirm_live_execution(action: str, ticker: str, qty: int,
                           side: str, estimated_cost: float = 0.0) -> bool:
    """Triple-confirmation gate for live orders (Factor 3).

    In PAPER mode: returns True immediately (no extra prompt needed).
    In LIVE mode: displays a red warning panel and requires the user to
    type the confirmation phrase exactly. Logs the attempt to the
    live_audit table regardless of outcome.

    Parameters:
        action:         Human-readable description (e.g., "BUY 10x AAPL")
        ticker:         The ticker symbol
        qty:            Number of shares/units
        side:           "BUY" or "SELL"
        estimated_cost: Approximate dollar value of the order

    Returns:
        True if the order should proceed, False if rejected.
    """

def log_live_audit(ticker: str, side: str, qty: int,
                   estimated_cost: float, outcome: str,
                   rejection_reason: str = '') -> None:
    """Write a row to the live_audit table.

    Called automatically by confirm_live_execution(). Also available
    for direct use by modules that need to log order outcomes.

    Parameters:
        ticker:           The ticker symbol
        side:             "BUY" or "SELL"
        qty:              Number of shares/units
        estimated_cost:   Approximate dollar value
        outcome:          "CONFIRMED", "REJECTED", "ERROR", "PAPER_PASSTHROUGH"
        rejection_reason: Why the order was rejected (empty if confirmed)
    """

def init_live_audit_table() -> None:
    """Create the live_audit table if it does not exist.

    Called from db.py init_db(). Uses CREATE TABLE IF NOT EXISTS
    for backwards compatibility.
    """
```

### DB Schema: `live_audit` Table

```sql
CREATE TABLE IF NOT EXISTS live_audit (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,  -- ISO 8601 UTC
    ticker           TEXT    NOT NULL,
    side             TEXT    NOT NULL,  -- 'BUY' or 'SELL'
    qty              INTEGER NOT NULL,
    estimated_cost   REAL    NOT NULL DEFAULT 0.0,
    trading_mode     TEXT    NOT NULL,  -- 'PAPER' or 'LIVE'
    outcome          TEXT    NOT NULL,  -- 'CONFIRMED', 'REJECTED', 'ERROR', 'PAPER_PASSTHROUGH'
    rejection_reason TEXT    NOT NULL DEFAULT '',
    client_ip        TEXT    NOT NULL DEFAULT '',  -- for future multi-session forensics
    session_id       TEXT    NOT NULL DEFAULT ''   -- unique per CLI invocation
);

CREATE INDEX IF NOT EXISTS idx_live_audit_ticker ON live_audit(ticker);
CREATE INDEX IF NOT EXISTS idx_live_audit_timestamp ON live_audit(timestamp);
```

### Integration Points

**1. `globals.py` changes:**

Replace the current hardcoded `TradingClient` construction:

```python
# BEFORE (current):
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
LIVE_MODE = False

# AFTER:
from buffet_bot.live_guard import is_live_mode, get_trading_client
LIVE_MODE = is_live_mode()
trading_client = get_trading_client()
```

The `LIVE_MODE` constant remains importable from `globals.py` by all modules, preserving all existing import paths. The only behavioral change is that it now reflects reality instead of being hardcoded False.

**2. `crypto.py` change (line 354):**

Replace the inline `TradingClient` construction:

```python
# BEFORE:
_client = _TC(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)

# AFTER:
from buffet_bot.live_guard import get_trading_client
_client = get_trading_client()
```

**3. All `--execute` paths (9 call sites):**

Each call site wraps its order submission with `confirm_live_execution()`. Example for `cmd_trading.py` analyze command:

```python
# BEFORE:
if not dry_run and result['consensus'] == 'BUY':
    if click.confirm(f'Execute BUY {ticker}? (Paper)'):
        _place_order(ticker, best)

# AFTER:
from buffet_bot.live_guard import confirm_live_execution
if not dry_run and result['consensus'] == 'BUY':
    mode_label = "LIVE" if LIVE_MODE else "Paper"
    if click.confirm(f'Execute BUY {ticker}? ({mode_label})'):
        if confirm_live_execution("BUY", ticker, best.get('qty', 1), "BUY",
                                   estimated_cost=best.get('qty', 1) * realtime.get('price', 0)):
            _place_order(ticker, best)
```

In paper mode, `confirm_live_execution()` returns True immediately and logs a `PAPER_PASSTHROUGH` audit row. In live mode, it shows the red warning panel and requires explicit confirmation.

**4. `_place_order()` in `analysis.py`:**

`_place_order()` itself does NOT call `confirm_live_execution()`. The guard sits at each caller, not inside `_place_order()`, because:
- Some callers (like `automate`) have non-interactive flows where the confirmation must happen earlier
- The guard needs context (action description, estimated cost) that `_place_order()` does not have
- Putting the guard in callers keeps `_place_order()` a simple, testable order-submission function

**5. `db.py` change:**

Add `init_live_audit_table()` call inside `init_db()`:

```python
def init_db():
    # ... existing CREATE TABLE statements ...
    from buffet_bot.live_guard import init_live_audit_table
    init_live_audit_table()
```

### Reconciliation with ADR-004

ADR-004 states: "`paper=True` is hardcoded. There is no flag, config option, or environment variable to switch to live trading. This decision is **permanent and non-negotiable**."

This ADR **amends** ADR-004 as follows:

- **ADR-004 remains the default behavior.** With zero configuration, `is_live_mode()` returns False, `get_trading_client()` returns a paper client, and the system behaves identically to today.
- **ADR-004's intent is preserved.** The triple-confirmation design ensures live mode cannot be activated accidentally, by automation, or by a single misconfiguration. It requires deliberate, multi-step human action.
- **ADR-004's status changes to:** "Accepted (amended by ADR-011 — paper remains default; live requires triple confirmation)"
- The phrase "non-negotiable" in ADR-004 applied to the v0.1.0-v0.5.0 era when no safety infrastructure existed. ADR-011 provides that infrastructure.

### Complete Call Site Inventory (for ENG reference)

| # | Module | Function/Command | Order Side | Current Guard | Integration |
|---|--------|------------------|------------|---------------|-------------|
| 1 | `analysis.py:211` | `_place_order()` | BUY | None (callers guard) | No change -- callers add guard |
| 2 | `cmd_trading.py:366` | `analyze --execute` | BUY | `click.confirm` | Add `confirm_live_execution()` |
| 3 | `cmd_trading.py:430` | `buy` command | BUY | `click.confirm` | Add `confirm_live_execution()` |
| 4 | `cmd_portfolio.py:114` | `rebalance --execute` | BUY (multi) | `click.confirm` per order | Add `confirm_live_execution()` per order |
| 5 | `cmd_portfolio.py:338` | `check-sells --execute` | SELL (multi) | `click.confirm` | Add `confirm_live_execution()` per sell |
| 6 | `cmd_account.py:317` | `automate buy_stock` tool | BUY | `execute` flag only | Add `confirm_live_execution()` |
| 7 | `plans.py:192` | plan execution | BUY | `Prompt.ask` + optional confirm | Add `confirm_live_execution()` |
| 8 | `plans.py:238` | `_guide_single_stock` | BUY | `click.confirm` | Add `confirm_live_execution()` |
| 9 | `crypto.py:345` | crypto order | BUY | upstream confirm | Add `confirm_live_execution()` + replace `_TC(paper=True)` |

**Rationale:**

Alternatives considered:
1. **Single env var (`BUFFET_BOT_LIVE=1` only):** Too easy to set accidentally. A single `export` command or `.env` copy-paste enables real-money trading. Rejected.
2. **Config file toggle:** Config files are meant for preferences, not safety-critical gates. A misedited TOML file should not cost the user money. Rejected.
3. **Guard inside `_place_order()` only:** Would miss the confirmation UX context. Would also make `_place_order()` side-effectful (prompting for input), breaking its role as a simple order submitter. Rejected.
4. **Separate live binary / entry point:** Too much code duplication and would fragment the CLI surface. Rejected.

The triple-confirmation pattern (env var + secret + interactive prompt) is a well-established pattern in infrastructure tooling (e.g., Terraform's `-auto-approve` still requires state configuration, AWS CLI requires both `--profile` and `--region` for destructive operations).

**Consequences:**

- **Enables:** v0.6.0 live trading, `compound` command, `automate --sweep`, full `LIVE_MODE` activation
- **Enables:** forensic audit trail of every order attempt via `live_audit` table
- **Constrains:** Every new command that places orders MUST call `confirm_live_execution()` -- this is a pattern requirement (add to PATTERNS.md)
- **Constrains:** No order can be placed in live mode without a human typing confirmation -- this rules out fully autonomous live trading (by design)
- **Risk:** The `automate` command's ReAct loop currently places orders non-interactively via `buy_stock()`. In live mode, each `buy_stock()` call will trigger an interactive confirmation, breaking the autonomous flow. This is intentional -- fully autonomous live trading is explicitly out of scope.
- **Migration:** Existing users see zero behavior change until they deliberately set both `BUFFET_BOT_LIVE=1` and `BUFFET_BOT_LIVE_SECRET`. No existing `.env` files contain these variables.

---

## ADR-010: Concurrency model — ThreadPoolExecutor over asyncio
- **Date:** 2026-03-04
- **Status:** Accepted
- **Decided by:** Software Engineer Agent (session 14)

**Context:** The v0.5.0 ROADMAP had an open PERF question: should the concurrent data fetch in `_run_analysis()` migrate from `concurrent.futures.ThreadPoolExecutor` to `asyncio`? The dead `import asyncio` (D-001, now removed) was a leftover from this open question.

**Decision:** Retain `ThreadPoolExecutor`. Do not migrate to `asyncio`.

**Rationale:**
1. **All I/O is synchronous by design.** Every external call (`yfinance.download`, `requests.get`, `ollama.chat`, Alpaca SDK methods) uses synchronous blocking libraries that have no async variants. Wrapping them in `asyncio.run_in_executor` would give identical behaviour to `ThreadPoolExecutor.submit` while adding indirection and complexity.
2. **ThreadPoolExecutor is already effective.** `_run_analysis()` in `analysis.py` fans out 8–9 I/O calls concurrently with `ThreadPoolExecutor(max_workers=9)`, then collects results. The bottleneck is network latency and Ollama inference time — both of which are irreducible regardless of the concurrency model.
3. **LLM calls are sequential by necessity.** `analyze_news_sentiment()` and the two LLM queries in `_run_analysis()` are run sequentially after the concurrent I/O phase because each depends on prior results. `asyncio` does not help here.
4. **Complexity cost.** Adding `asyncio` to a Click CLI requires `asyncio.run()` wrappers at every command entry point or a third-party bridge (`click-async`). This is non-trivial, increases risk of event-loop conflicts with libraries that also use event loops internally, and provides no measurable benefit.
5. **Existing code is clean and readable.** The `with ThreadPoolExecutor(max_workers=9) as ex: / f_x = ex.submit(...)` pattern is immediately understandable without async/await syntax.

**Consequences:**
- The `import asyncio` that was removed in D-001 must not be re-added until there is a concrete, benchmarked reason.
- New concurrent I/O must continue to use `ThreadPoolExecutor.submit()`. Each new fetch function is a plain synchronous function.
- If a future dependency ships a native async client (e.g., a hypothetical `aioalpaca`), re-open this ADR with a measured benchmark before migrating.

---

## ADR-007: Amnesia clause for all agents
- **Date:** 2026-02-28
- **Status:** Accepted
- **Decided by:** User + Product Manager

**Context:** Cross-session memory files (auto-memory) were found to cause agents to act on stale assumptions about function names, command counts, and architecture that no longer matched the actual code.

**Decision:** Every agent `.md` file includes an amnesia clause requiring the agent to read source files from disk at the start of each session before taking any action.

**Rationale:** The codebase changes faster than memory files are updated. Stale memory is worse than no memory because it creates false confidence. Reading the actual files takes seconds and is always accurate.

**Consequences:** Agents spend the first part of each session reading `main.py`. This is intentional and expected. Do not skip this step to "save time."

---

## ADR-006: Multi-agent system via per-role `.md` instruction files
- **Date:** 2026-02-28
- **Status:** Accepted
- **Decided by:** User

**Context:** Building Buffet-Bot to maximum capacity requires parallel work across different specializations: product, engineering, architecture, UI, and data sourcing. A single-agent approach serializes work that could be parallel.

**Decision:** Each role gets its own Claude Code instruction file in `agents/`. Agents coordinate through shared files on disk (`ROADMAP.md`, `PATTERNS.md`, `SCHEMA.md`, etc.) rather than live communication.

**Rationale:** File-based coordination is durable, reviewable, and doesn't require a live multi-agent orchestration system. Each agent is a standalone Claude Code session with a specialized system prompt.

**Consequences:** Agents must write their outputs to shared files for other agents to consume. The Product Manager owns the coordination layer. Engineers must check `PATTERNS.md` before implementing to avoid conflicting approaches.

---

## ADR-005: Intentional monolith — all logic in `buffet_bot/main.py`
- **Date:** (estimated v0.1.0)
- **Status:** Accepted (re-review at >3000 lines)
- **Decided by:** Original developer

**Context:** The project started as a simple CLI script and grew to ~2100 lines. There were multiple opportunities to split into modules.

**Decision:** Keep all logic in a single `buffet_bot/main.py` file until there is a concrete, justified reason to split.

**Rationale:** Monoliths are simpler to navigate, edit, and reason about for a solo/small-team project at this stage. Module splits introduce import complexity, circular dependency risks, and coordination overhead that aren't justified yet.

**Consequences:** The Architect must actively monitor line count. When any logical domain (e.g., backtesting) exceeds ~500 lines and grows independently, raise a split proposal. Approved split structure is documented in `architect.md`.

---

## ADR-004: Paper trading only — `paper=True` hardcoded, not configurable
- **Date:** (estimated v0.1.0)
- **Status:** Accepted (amended by ADR-011 — paper remains default; live requires triple confirmation)
- **Decided by:** Original developer

**Context:** Buffet-Bot makes autonomous trading decisions driven by LLMs. Allowing real-money execution would create serious financial risk for users.

**Decision:** `TradingClient(API_KEY, SECRET_KEY, paper=True)` is hardcoded. There is no flag, config option, or environment variable to switch to live trading.

**Rationale:** LLM-driven trading without human oversight is inappropriate for real capital. The paper trading constraint is a safety guarantee, not a limitation.

**Consequences:** This decision is **permanent and non-negotiable**. Any agent or pull request that changes `paper=True` to `paper=False` must be rejected. The README and security note make this explicit.

---

## ADR-003: Local LLMs via Ollama only — no cloud AI API calls
- **Date:** (estimated v0.1.0)
- **Status:** Accepted
- **Decided by:** Original developer

**Context:** Many trading assistants require OpenAI or Anthropic API subscriptions, creating recurring costs and data privacy concerns (sending financial data to external servers).

**Decision:** All LLM inference runs locally via Ollama. No OpenAI, Anthropic, Gemini, or other cloud AI API keys are used. The models are `deepseek-r1` and `qwen2.5:7b`.

**Rationale:** Local inference is free after one-time model download, preserves user data privacy, and works offline. This is a core differentiator of Buffet-Bot.

**Consequences:** New features cannot add cloud LLM calls. If a feature needs a capability that local models can't provide, find a different approach (deterministic algorithm, free public API, etc.).

---

## ADR-002: Free-tier data sources only
- **Date:** (estimated v0.1.0)
- **Status:** Accepted
- **Decided by:** Original developer

**Context:** Buffet-Bot targets individual investors who should not need to pay for data subscriptions on top of a brokerage account.

**Decision:** All data sources must have a usable free tier. Current approved sources: `yfinance` (free), Alpaca Data API (free with paper account), Alpaca News API (free with paper account).

**Rationale:** Paid data subscriptions (Bloomberg, Refinitiv, etc.) are cost-prohibitive for the target user. Free-tier sources provide sufficient data for Buffett-style fundamental analysis.

**Consequences:** The Web Scraper agent must evaluate rate limits and free tier viability before recommending any new data source. No source requiring a credit card for free tier is acceptable without explicit user approval.

---

## ADR-001: SQLite for local persistence — no external database
- **Date:** (estimated v0.2.0)
- **Status:** Accepted
- **Decided by:** Original developer

**Context:** Buffet-Bot needed persistent storage for recommendation history and outcomes without requiring users to set up a database server.

**Decision:** Use Python's built-in `sqlite3` module. Database file lives at `~/.buffet-bot.db`. Tables: `recommendations`, `outcomes`.

**Rationale:** SQLite requires no setup, is file-based, travels with the user's home directory, and handles the expected volume (hundreds to thousands of rows) without any performance concerns. No ORM is needed at this scale.

**Consequences:** Schema changes must be backwards-compatible using `ALTER TABLE ... ADD COLUMN`. The `init_db()` function handles migration idempotently via `CREATE TABLE IF NOT EXISTS`. See `SCHEMA.md` for full schema reference.

---

## ADR-009: Config file format — TOML at `~/.buffet-bot-config.toml`
- **Date:** 2026-02-28
- **Status:** Accepted — PM approved implementation 2026-02-28
- **Decided by:** Architect Agent → PM approved

**Context:** ROADMAP v0.4.1 lists `~/.buffet-bot-config.toml` for user preferences. The open question in this file listed `.toml` vs `.json` vs more env vars.

**Decision:** Use TOML (via Python stdlib `tomllib` / `tomli` write shim) at `~/.buffet-bot-config.toml`.

**Rationale:**
- `tomllib` is stdlib in Python 3.11+ (read-only). For writing, `tomli-w` is a small, single-purpose package.
- TOML is more human-readable than JSON for config files — inline comments, no trailing comma errors.
- `.env` stays for secrets only (API keys). Config file is for user preferences only — no secrets.
- JSON is already used for plan files (`~/.buffet-plans/`) — having a separate format for config reduces confusion about what each file type contains.

**Config schema (proposed):**
```toml
# ~/.buffet-bot-config.toml

[defaults]
model    = "deepseek-r1"    # Primary Ollama model
risk     = "medium"          # low | medium | high
strategy = "value"           # value | growth | dividend | turnaround

[display]
buffett_score_green  = 70    # Score >= this → green
buffett_score_yellow = 40    # Score >= this → yellow (else red)
```

**Consequences:**
- ENG must add `tomli-w>=1.0.0` to `requirements.txt` and `pyproject.toml`
- Load config with `_load_config()` helper at module level (after `load_dotenv()`). Falls back to hardcoded defaults if file absent.
- Never store secrets in config — only in `.env`
- Config path: `CONFIG_PATH = os.path.expanduser("~/.buffet-bot-config.toml")`
- `_load_config()` must be silent on missing file (return defaults dict, not raise)

---

## ADR-008: `_analyze_crypto` migration to `crypto.py`
- **Date:** 2026-02-28
- **Status:** Accepted — PM approved implementation 2026-02-28
- **Decided by:** Architect Agent → PM approved

**Context:** `_analyze_crypto()` (lines 1204–1334, 131 lines) lives in `main.py` but is logically crypto domain logic. All other crypto code lives in `buffet_bot/crypto.py`.

**Decision:** Move `_analyze_crypto()` to `crypto.py`. The `crypto` CLI command in `main.py` calls it via import.

**Rationale:** Keeps crypto logic co-located, reduces `main.py` by ~131 lines (delays hitting the 3000-line threshold), and is consistent with how `volatile.py` and `politicians.py` own their domain logic.

**Implementation requirements:**
- `_analyze_crypto()` uses `console`, `MODELS`, `MODEL_COLORS`, `STRATEGY_PROMPTS`, `ollama`, `json`, `TradingClient` — these must be passed as parameters or the function must import them from a shared constants module.
- Simplest approach: pass `console`, `models_list`, `model_colors`, `strategy_prompts` as parameters. Avoids circular imports.
- The `crypto` CLI command in `main.py` becomes a thin wrapper calling `crypto.analyze_crypto(symbol, dry_run, primary_model, console, MODELS, MODEL_COLORS, STRATEGY_PROMPTS)`.

**Consequences:** `crypto.py` grows by ~131 lines but remains within a single domain. `main.py` shrinks by same amount.

---

## Open Questions (Undecided)

These are decisions that haven't been made yet. The Product Manager should facilitate decisions on these when they become relevant.

| Question | Context | Status |
|----------|---------|--------|
| When to split `main.py` into modules | Now 2760 lines; threshold 3000 | **Decided:** split executed session 10; 13 modules; see AUDIT.md |
| Config file format | User preferences (model, risk, strategy) | **Decided:** TOML at `~/.buffet-bot-config.toml` — see ADR-009 |
| Live trading safety | How to enable live mode without accidental activation | **Decided:** triple-confirmation in `live_guard.py` — see ADR-011; implemented and verified session 16 |
| Async LLM queries | Two models queried sequentially — slow | **Decided:** retain ThreadPoolExecutor — see ADR-010 |
| Compounding Engine schema | `compound_log` and `sweeps` table design | **Decided:** see ADR-012; ENG session 17 implements |
| Edge Score architecture | Multi-factor composite score replacing Buffett-only ranking | **Decided:** see ADR-013; dedicated ENG session after session 17 |
| Options Engine architecture | Covered call / CSP selection logic, position tracking | **Decided:** see ADR-014; requires edge.py first |
| Macro Regime Engine architecture | FRED regime classifier, sector rotation | **Decided:** see ADR-015; ENG session for v0.9.0 |
| PyPI distribution | Making `pip install buffet-bot` work | Open — needs version tagging strategy |
| Multi-model expansion | Users want `llama3`, `mistral`, etc. | Open — MODELS list is hardcoded; no decision made |

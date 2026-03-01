# Database Schema — Buffet-Bot

> Owned by: Architect Agent
> Last verified against: `buffet_bot/main.py` `init_db()` (lines 87–115)
> DB file: `~/.buffet-bot.db` (SQLite 3)

---

## Rules for All Schema Changes

1. **Never drop or rename existing columns** — this would break existing user databases
2. Add columns with `ALTER TABLE ... ADD COLUMN col_name TYPE NOT NULL DEFAULT value`
3. New tables use `CREATE TABLE IF NOT EXISTS` inside `init_db()` — idempotent by design
4. Schema changes must be backwards-compatible: a DB created on v0.1 must still open on v1.0
5. Document every change in the **Migration Log** section at the bottom of this file
6. String truncation: `reason` is capped at 500 chars in `log_recommendation()` — keep this

---

## Table: `recommendations`

Logged automatically on every BUY consensus from `_run_analysis()`.

```sql
CREATE TABLE IF NOT EXISTS recommendations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT    NOT NULL,                  -- ISO 8601 UTC, e.g. "2025-03-01T14:22:01+00:00"
    ticker        TEXT    NOT NULL,                  -- uppercase, e.g. "AAPL"
    action        TEXT    NOT NULL,                  -- always "BUY" (only BUY is logged)
    confidence    REAL    NOT NULL DEFAULT 0.0,      -- LLM confidence 0.0–1.0
    qty           INTEGER NOT NULL DEFAULT 0,        -- shares recommended by LLM
    entry_price   REAL    NOT NULL DEFAULT 0.0,      -- live price at time of recommendation
    reason        TEXT    NOT NULL DEFAULT '',       -- LLM reason string, truncated to 500 chars
    model         TEXT    NOT NULL DEFAULT '',       -- primary model used, e.g. "deepseek-r1"
    strategy      TEXT    NOT NULL DEFAULT 'value',  -- one of: value, growth, dividend, turnaround
    buffett_score INTEGER NOT NULL DEFAULT 0         -- 0–100 integer score at time of recommendation
);
```

### Column Notes

| Column | Source | Example |
|--------|--------|---------|
| `timestamp` | `datetime.now(timezone.utc).isoformat()` | `"2025-03-01T14:22:01.123456+00:00"` |
| `ticker` | CLI argument, uppercased | `"MSFT"` |
| `action` | Always `"BUY"` — only BUY consensus rows are logged | `"BUY"` |
| `confidence` | `best_buy_resp.get('confidence', 0.0)` | `0.87` |
| `qty` | `best_buy_resp.get('qty', 0)` — LLM-suggested, may differ from ATR-sized qty | `12` |
| `entry_price` | `realtime.get('price', 0.0)` — Alpaca mid price or yfinance fallback | `182.45` |
| `reason` | `best_buy_resp.get('reason', '')[:500]` | `"Strong ROE and..."` |
| `model` | Primary model name passed to `_run_analysis()` | `"deepseek-r1"` |
| `strategy` | Strategy string from `--strategy` flag | `"value"` |
| `buffett_score` | `buffett.get('score', 0)` — integer from `get_buffett_metrics()` | `75` |

---

## Table: `outcomes`

Populated manually or by a future `track-outcomes` command. Currently has no automated writer.

```sql
CREATE TABLE IF NOT EXISTS outcomes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER NOT NULL REFERENCES recommendations(id),  -- FK to recommendations
    exit_timestamp    TEXT    NOT NULL,      -- ISO 8601 UTC when position was closed
    exit_price        REAL    NOT NULL DEFAULT 0.0,   -- price at close
    pnl_pct           REAL    NOT NULL DEFAULT 0.0,   -- (exit - entry) / entry * 100
    holding_days      INTEGER NOT NULL DEFAULT 0,     -- calendar days held
    outcome_note      TEXT    NOT NULL DEFAULT ''     -- free-text annotation
);
```

### Column Notes

| Column | Description |
|--------|-------------|
| `recommendation_id` | FK to `recommendations.id` — links exit to the original BUY recommendation |
| `pnl_pct` | Signed percentage: positive = profit, negative = loss |
| `holding_days` | Integer calendar days between entry and exit |
| `outcome_note` | Optional free-text: "stopped out", "target reached", etc. |

---

## Helper Functions (in `main.py`)

| Function | Signature | Purpose |
|----------|-----------|---------|
| `init_db()` | `() → None` | Creates both tables if absent. Called at module load (line 157). Idempotent. |
| `log_recommendation(...)` | See below | Inserts one row into `recommendations`. Silent on any exception. |
| `get_recent_recommendations(days)` | `(int) → list[dict]` | Returns rows from last N days as list of dicts keyed by column name. |

```python
# log_recommendation full signature:
log_recommendation(
    ticker: str,
    action: str,          # always 'BUY'
    confidence: float,
    qty: int,
    entry_price: float,
    reason: str,          # truncated to 500 chars internally
    model: str,
    strategy: str,
    buffett_score: int,
)
```

---

## Table: `watchlist`

User-managed ticker list. Populated by `watchlist add` / `watchlist remove` commands.

```sql
CREATE TABLE IF NOT EXISTS watchlist (
    ticker   TEXT PRIMARY KEY,     -- uppercase ticker, e.g. "TSLA"
    added_at TEXT NOT NULL         -- ISO 8601 UTC timestamp
);
```

### Column Notes

| Column | Source | Example |
|--------|--------|---------|
| `ticker` | `ticker.upper()` from CLI argument | `"TSLA"` |
| `added_at` | `datetime.now(timezone.utc).isoformat()` | `"2026-02-28T10:30:00+00:00"` |

### Helper Functions

| Function | Signature | Purpose |
|----------|-----------|---------|
| `add_to_watchlist(ticker)` | `(str) → None` | `INSERT OR IGNORE` — silent if already present |
| `remove_from_watchlist(ticker)` | `(str) → None` | `DELETE WHERE ticker = ?` — silent if not found |
| `get_watchlist()` | `() → list[dict]` | Returns `[{'ticker': ..., 'added_at': 'YYYY-MM-DD'}]` sorted alphabetically |

**Note:** The `added_at` field is truncated to date-only (`[:10]`) in `get_watchlist()` for display, but stored as full ISO timestamp in the DB.

---

## Planned Future Tables

Proposed additions (not yet implemented):
- `alerts` — price/RSI threshold alerts with ticker, threshold type, value
- `model_performance` — track LLM recommendation outcomes by model

---

## Migration Log

| Version | Date | Change |
|---------|------|--------|
| v0.2.0 | (original) | Created `recommendations` and `outcomes` tables |
| v0.4.1 | 2026-02-28 | Added `watchlist` table (ticker TEXT PRIMARY KEY, added_at TEXT) |

> When you add a migration: add a row here AND add the `ALTER TABLE` statement to `init_db()` so existing users get the column automatically.

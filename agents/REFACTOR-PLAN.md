# Token-Reduction Refactor Plan

> **Author:** Architect Agent (session 20)
> **Date:** 2026-03-06
> **Goal:** Reduce per-file token count for faster LLM comprehension without changing functionality.
> **Constraint:** All 248 existing tests must pass after every change. No behavioral changes.

---

## Summary

Total codebase: **9,052 lines** across 24 Python files in `buffet_bot/`.

| Category | Estimated Savings |
|----------|------------------|
| Unused imports in `globals.py` | ~30 lines |
| `_COMPANY_DB` extraction to data file | ~390 lines from `universe.py` |
| Color-upgrade boilerplate dedup | ~25 lines |
| DB connection boilerplate (context manager) | ~70 lines from `db.py` |
| Rich import consolidation via `display.py` | ~20 lines across cmd files |
| Dead/duplicate import cleanup across all files | ~15 lines |
| **Total estimated reduction** | **~550 lines (~6%)** |

---

## File-by-File Plan

### 1. `buffet_bot/globals.py` (255 lines -> ~220 lines, -35)

**Problem:** Contains 15+ unused imports left over from the monolith split. These were needed when everything lived in `main.py` but are now imported directly by their respective modules. Two of these (`StockLatestQuoteRequest`, `StockLatestBarRequest`) are re-exported to `data.py` and must be handled carefully.

**Unused imports to remove (verified: no downstream consumer imports these from globals):**
- `sqlite3` (line 12)
- `math` (line 13)
- `itertools` (line 14)
- `numpy as np` (line 15)
- `Counter, deque` from `collections` (line 16)
- `click` (line 6)
- `MarketOrderRequest, GetOrdersRequest` from `alpaca.trading.requests` (line 19)
- `OrderSide, TimeInForce, QueryOrderStatus` from `alpaca.trading.enums` (line 20)
- `yfinance as yf` (line 23)
- `pandas as pd` (line 24)
- `ollama` (line 25)
- `ThreadPoolExecutor, as_completed` from `concurrent.futures` (line 11)
- `Table, Prompt, Text` from rich (lines 32-34) -- `Panel` is used in `ensure_ollama_running()`
- `plotext as plt` (line 36)
- `requests` duplicate: line 8 (`import requests`) AND line 9 (`import requests as _req`). Only `_req` is used. Remove line 8.

**Re-export fix:** `data.py` lines 17-19 import `StockLatestQuoteRequest` and `StockLatestBarRequest` from `buffet_bot.globals`. These are Alpaca SDK classes. After removing them from globals, update `data.py` to import directly:
```python
# data.py: replace
#   from buffet_bot.globals import (StockLatestQuoteRequest, StockLatestBarRequest,)
# with:
from alpaca.data.requests import StockLatestQuoteRequest, StockLatestBarRequest
```

**Action:**
1. Remove all unused imports from `globals.py`.
2. Update `data.py` to import Alpaca data classes directly from `alpaca.data.requests`.
3. Run `pytest tests/` -- all 248 tests must pass.

---

### 2. `buffet_bot/universe.py` (495 lines -> ~110 lines, -385)

**Problem:** `_COMPANY_DB` is a 366-entry Python dict literal consuming ~390 lines. It is pure static data that never changes at runtime. Inlining it in a `.py` file wastes tokens every time an LLM reads the module.

**Action:** Extract `_COMPANY_DB` to a JSON data file and load it at import time.

1. Create `buffet_bot/data/companies.json` (or `buffet_bot/companies.json` to keep it flat):
   ```python
   # Script to generate: json.dumps(_COMPANY_DB, indent=2)
   ```
2. Replace the 390-line dict literal in `universe.py` with:
   ```python
   import json as _json
   from pathlib import Path

   _COMPANY_DB: dict[str, dict] = _json.loads(
       (Path(__file__).parent / "companies.json").read_text()
   )
   ```
3. Verify: `list_companies()`, `search_companies()`, `SECTORS`, and `_COMMON_TICKERS` in `data.py` all still work.
4. Run `pytest tests/` -- all 248 tests must pass.

**Token impact:** ~390 lines of repetitive dict entries removed from the Python file. The JSON file exists on disk but is never read by LLM agents during code review.

---

### 3. `buffet_bot/display.py` (80 lines -> ~100 lines, +20 net but saves ~25 elsewhere)

**Problem:** A color-upgrade pattern (`if color == 'cyan': color = 'bright_cyan'; elif color == 'magenta': color = 'bright_magenta'`) is copy-pasted in **5 locations** across 3 files:
- `display.py` line 21-24 (inside `_print_ai_responses`)
- `cmd_trading.py` lines 71-74, 678-681, 1264-1267
- `cmd_intel.py` lines 117-120

**Action:** Add a `_bright_color(color)` helper to `display.py`:
```python
def _bright_color(color: str) -> str:
    """Upgrade dim color names to their bright equivalents for vibrant UI."""
    _MAP = {'cyan': 'bright_cyan', 'magenta': 'bright_magenta'}
    return _MAP.get(color, color)
```

Then replace all 5 occurrences of the 4-line `if/elif` block with a single call:
```python
color = _bright_color(MODEL_COLORS.get(model, 'bright_cyan'))
```

**Savings:** 5 x 4 lines = 20 lines removed, 5 x 1 line added + 4-line helper = net ~15 lines saved. More importantly, it eliminates a maintenance risk (if a third color is added to `MODEL_COLORS`, all 5 sites need updating).

---

### 4. `buffet_bot/db.py` (525 lines -> ~455 lines, -70)

**Problem:** Every DB function repeats the same `sqlite3.connect(DB_PATH)` / `conn.commit()` / `conn.close()` pattern wrapped in a bare `try/except`. There are **21 separate `sqlite3.connect(DB_PATH)` calls**, each with its own `try/except/commit/close`. This is the largest source of boilerplate in the file.

**Action:** Add a context manager at the top of `db.py`:
```python
from contextlib import contextmanager

@contextmanager
def _db():
    """Yield a SQLite connection that auto-commits and closes."""
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
```

Then refactor each function to use it. Example:
```python
# Before (7 lines):
def add_to_watchlist(ticker):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR IGNORE INTO watchlist ...", ...)
        conn.commit()
        conn.close()
    except Exception:
        pass

# After (4 lines):
def add_to_watchlist(ticker):
    try:
        with _db() as conn:
            conn.execute("INSERT OR IGNORE INTO watchlist ...", ...)
    except Exception:
        pass
```

Each function saves 2-3 lines. Across 14+ functions that follow this pattern, the savings compound to ~40-70 lines.

**Important:** The `init_db()` and `init_compound_tables()` functions use `executescript()` which auto-commits, so those can skip the explicit commit. Adjust accordingly.

**Risk:** Low. The context manager is a pure refactor of the connection lifecycle. No query changes.

---

### 5. `buffet_bot/cmd_trading.py` (1273 lines -> ~1245 lines, -28)

**Problem areas identified:**
- Color-upgrade boilerplate (3 instances, ~12 lines) -- addressed by item 3 above.
- `from rich.panel import Panel` / `from rich.table import Table` / `from rich.prompt import Prompt` / `from rich import box` -- these are imported in every cmd file. They could be re-exported from `display.py` but the savings are marginal (~1-2 lines per file). **Not recommended** -- the explicit imports are clearer for engineers.

**Additional cleanup:**
- Line 44: `import requests` is imported at the bottom of the import block, separated from the top-level imports. Move to the top import group for consistency (no line savings, but better readability).
- The `_fetch_spy_benchmark()` helper (lines ~600-640) and `_annualised_cagr()` (lines ~595-600) are only used by the `portfolio` command. They are fine where they are -- no extraction needed.

**Action:**
1. Replace 3 color-upgrade blocks with `_bright_color()` calls (per item 3).
2. Move `import requests` to the top import group.

---

### 6. `buffet_bot/cmd_account.py` (1212 lines -> ~1210 lines, -2)

**Problem areas identified:**
- Only 1 instance of color-upgrade boilerplate (the file mostly uses `_make_panel_title` which handles color internally). Minimal duplication.
- The file is long (1212 lines, 8 commands) but each command is self-contained. Splitting further would fragment the "account management" domain without justification.
- `from buffet_bot.db import` appears twice (lines 29-33 and line 42). These could be merged into a single import statement.

**Action:**
1. Merge the two `from buffet_bot.db import` blocks into one.
2. No further changes recommended -- the file is well-structured for its size.

---

### 7. `buffet_bot/cmd_portfolio.py` (1122 lines -> ~1118 lines, -4)

**Problem areas identified:**
- The `compound` command (lines ~870-1122, ~250 lines) is the largest single command in the file. It is self-contained and does not share logic with other commands in the file. If it grows further (e.g., v0.8.0 options income integration), consider extracting to its own file.
- Line 869: `from buffet_bot.globals import DB_PATH` is a local import inside `_fetch_realized_profits()`. This is the only place in the file that touches SQLite directly -- it should ideally call a `db.py` helper instead. **Deferred** -- this is a functional change that should be a separate ticket.
- Lines 1-17: standard import block with `import sqlite3` (only used in one spot). Could be removed if the DB query is moved to `db.py`.

**Action:**
1. No immediate changes recommended. The file is at a healthy size.
2. **Future (v0.8.0+):** If `compound` grows past 300 lines, extract to `buffet_bot/compound.py`.
3. **Future:** Move `_fetch_realized_profits()` SQL query to `db.py` as `get_realized_profits(days)`.

---

### 8. `buffet_bot/cmd_intel.py` (525 lines -> ~520 lines, -5)

**Problem areas identified:**
- 1 color-upgrade boilerplate instance (lines 117-120) -- addressed by item 3.
- `from buffet_bot.crypto import` appears twice (lines 17 and 28-31). Merge into one.

**Action:**
1. Replace color-upgrade block with `_bright_color()` call.
2. Merge duplicate crypto imports.

---

## Files That Need No Changes

These files are appropriately sized and have no significant duplication:

| File | Lines | Assessment |
|------|-------|------------|
| `analysis.py` | 225 | Clean; single-purpose |
| `backtest.py` | 178 | Clean; single-purpose |
| `risk.py` | 191 | Clean; single-purpose |
| `projections.py` | 128 | Clean; single-purpose |
| `plans.py` | 377 | Moderate size but well-structured |
| `data.py` | 346 | Clean after import fix |
| `crypto.py` | 370 | Self-contained domain module |
| `automate.py` | 289 | Clean; `SWEEP_AGENT_PROMPT` is a large string but necessary |
| `insiders.py` | 276 | Clean |
| `politicians.py` | 214 | Clean |
| `ibkr.py` | 212 | Clean |
| `edge.py` | 204 | Clean; recently written |
| `volatile.py` | 183 | Clean |
| `live_guard.py` | 272 | Clean; deliberately uses local imports to avoid circular deps |
| `main.py` | 99 | Minimal dispatcher; no changes needed |
| `__init__.py` | 1 | N/A |

---

## Shared Extraction Candidates

### Already in `display.py`
- `_make_panel_title()` -- used 34 times across 5 files. Well-established.
- `_score_color()` -- used in `cmd_trading.py`, `cmd_portfolio.py`, `plans.py`. Good.
- `_consensus_text()` -- used in `cmd_trading.py`, `plans.py`. Good.
- `_change_color()` -- used in `cmd_trading.py`. Good.
- `_print_live_market()` -- used in `cmd_trading.py`, `cmd_intel.py`. Good.
- `_print_ai_responses()` -- used in `cmd_trading.py`. Good.

### Proposed additions to `display.py`
- `_bright_color(color)` -- new helper replacing 5 copy-pasted color-upgrade blocks.

### NOT recommended for extraction
- `ThreadPoolExecutor` patterns -- each usage has different `max_workers`, different future-result handling, and different error strategies. Abstracting this would be over-engineering. Per ADR-010, these stay as-is.
- Rich `Panel`/`Table`/`box` imports -- re-exporting from `display.py` would save 1 import line per file but obscure the dependency chain. Not worth it.

---

## Recommended Execution Order

The Software Engineer should execute these in order. Each step is independently committable and testable.

| Step | Files Modified | Description | Risk |
|------|---------------|-------------|------|
| 1 | `globals.py`, `data.py` | Remove unused imports from globals; fix data.py re-export | Low |
| 2 | `display.py` | Add `_bright_color()` helper | None |
| 3 | `cmd_trading.py`, `cmd_intel.py` | Replace color-upgrade boilerplate with `_bright_color()` | Low |
| 4 | `db.py` | Add `_db()` context manager; refactor connection pattern | Low |
| 5 | `universe.py` + new `companies.json` | Extract `_COMPANY_DB` to JSON data file | Low |
| 6 | `cmd_intel.py`, `cmd_account.py` | Merge duplicate import blocks | None |

**After each step:** Run `pytest tests/` and verify 248 tests pass. Run `python buffet-bot.py --help` to verify all commands still register.

---

## What This Plan Does NOT Cover

- **Runtime performance** -- this is a token-count/readability refactor, not a speed optimization.
- **Functional changes** -- no command behavior changes, no new features.
- **File splits** -- no files currently justify splitting. `cmd_trading.py` (1273 lines) is the largest but its 15 commands are all trading-domain and well-organized.
- **Test refactoring** -- test files are out of scope for this audit.
- **Moving `_fetch_realized_profits()` to db.py** -- this is a functional boundary change that should be its own ticket.

---

## Estimated Token Impact

Assuming ~0.75 tokens per character (typical for Python code):

| Change | Lines Removed | Est. Tokens Saved |
|--------|--------------|-------------------|
| globals.py unused imports | 30 | ~600 |
| universe.py _COMPANY_DB extraction | 385 | ~12,000 |
| color-upgrade dedup | 15 | ~300 |
| db.py context manager | 50 | ~1,000 |
| Import merges | 10 | ~200 |
| **Total** | **~490** | **~14,100** |

The `universe.py` extraction alone accounts for ~85% of the savings. The `_COMPANY_DB` dict is the single largest token consumer in the codebase relative to its information density.

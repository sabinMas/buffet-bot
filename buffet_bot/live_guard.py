"""Live Trading Guard — triple-confirmation safety layer for buffet-bot.

This module is the single source of truth for whether the system operates in
PAPER or LIVE mode. It implements a three-factor activation model:

  Factor 1: BUFFET_BOT_LIVE=1           (environment variable)
  Factor 2: BUFFET_BOT_LIVE_SECRET=...  (non-empty passphrase in env)
  Factor 3: Interactive confirmation     (per-order, runtime prompt)

ALL THREE factors must be satisfied for a live order to proceed.
If any factor is absent, the system remains in paper mode silently.

See ADR-011 in agents/DECISIONS.md for the full design rationale.

Usage by other modules:
    from buffet_bot.live_guard import (
        is_live_mode, get_trading_client,
        confirm_live_execution, init_live_audit_table,
    )
"""

import os
import sqlite3
import uuid
from datetime import datetime, timezone

import click
from rich.panel import Panel
from rich import box

# NOTE: buffet_bot.globals imports live_guard at module level, so we cannot
# import from globals at module level here (circular import). All globals
# references (API_KEY, SECRET_KEY, DB_PATH, console) are resolved lazily
# inside the functions that need them.

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Unique identifier for this CLI session — written to every audit row so that
# concurrent terminal sessions can be distinguished forensically.
_SESSION_ID: str = uuid.uuid4().hex[:12]

# Cache for the singleton TradingClient instance.
_trading_client_cache = None

# ---------------------------------------------------------------------------
# Factor 1 + 2: Mode detection
# ---------------------------------------------------------------------------


def is_live_mode() -> bool:
    """Check whether LIVE mode is activated (Factors 1 + 2).

    Returns True only when BOTH of the following are true:
      - The environment variable BUFFET_BOT_LIVE is set to exactly "1"
      - The environment variable BUFFET_BOT_LIVE_SECRET is set and non-empty

    Returns False in all other cases.  This is the safe default — with zero
    configuration, the system is always in paper mode.

    This function reads the environment on every call (no caching) so that
    tests can patch os.environ and get correct results.
    """
    live_flag   = os.getenv('BUFFET_BOT_LIVE', '').strip()
    live_secret = os.getenv('BUFFET_BOT_LIVE_SECRET', '').strip()
    return live_flag == '1' and len(live_secret) > 0


# ---------------------------------------------------------------------------
# TradingClient factory
# ---------------------------------------------------------------------------


def get_trading_client():
    """Return an Alpaca TradingClient with paper= derived from is_live_mode().

    The client is created once per process and cached.  This function replaces
    the direct ``TradingClient(API_KEY, SECRET_KEY, paper=True)`` construction
    that currently lives in globals.py.

    Returns:
        An ``alpaca.trading.client.TradingClient`` instance.

    Raises:
        ValueError: If API_KEY or SECRET_KEY are missing (same as globals.py
        does today).
    """
    global _trading_client_cache
    if _trading_client_cache is not None:
        return _trading_client_cache

    from buffet_bot.globals import API_KEY, SECRET_KEY
    from alpaca.trading.client import TradingClient
    _trading_client_cache = TradingClient(
        API_KEY, SECRET_KEY,
        paper=(not is_live_mode()),
    )
    return _trading_client_cache


# ---------------------------------------------------------------------------
# Factor 3: Per-order interactive confirmation
# ---------------------------------------------------------------------------


def confirm_live_execution(
    action: str,
    ticker: str,
    qty: int,
    side: str,
    estimated_cost: float = 0.0,
) -> bool:
    """Triple-confirmation gate for live orders (Factor 3).

    In PAPER mode:
        Returns True immediately (no extra prompt needed beyond the
        existing click.confirm that each command already shows).
        Logs a PAPER_PASSTHROUGH row to the audit table.

    In LIVE mode:
        Displays a prominent red warning panel explaining that this order
        will use REAL MONEY.  Requires the user to type a confirmation
        phrase exactly (e.g., "YES I CONFIRM").  If the user declines or
        types incorrectly, the order is rejected.  Logs the attempt
        (CONFIRMED or REJECTED) to the audit table.

    Parameters:
        action:         Human-readable description (e.g., "BUY 10x AAPL")
        ticker:         The ticker symbol
        qty:            Number of shares/units
        side:           "BUY" or "SELL"
        estimated_cost: Approximate dollar value of the order (0.0 if unknown)

    Returns:
        True if the order should proceed, False if rejected by the user
        or by a missing factor.
    """
    if not is_live_mode():
        log_live_audit(ticker, side, qty, estimated_cost, "PAPER_PASSTHROUGH")
        return True

    from buffet_bot.globals import console
    # -- LIVE MODE: show red warning panel --
    console.print(Panel(
        f"[bold bright_red]LIVE TRADING ORDER[/bold bright_red]\n\n"
        f"  Action:         {action}\n"
        f"  Ticker:         {ticker}\n"
        f"  Side:           {side}\n"
        f"  Quantity:       {qty}\n"
        f"  Est. cost:      ${estimated_cost:,.2f}\n\n"
        "[bright_red]This will execute with REAL MONEY.[/bright_red]\n"
        "[bright_red]This action cannot be undone.[/bright_red]",
        title="[bold bright_red]LIVE ORDER CONFIRMATION[/bold bright_red]",
        border_style="bright_red",
        box=box.HEAVY,
    ))

    response = click.prompt(
        'Type "YES I CONFIRM" to proceed, or anything else to cancel',
        default='',
        show_default=False,
    )
    if response.strip() == 'YES I CONFIRM':
        log_live_audit(ticker, side, qty, estimated_cost, "CONFIRMED")
        return True
    else:
        log_live_audit(ticker, side, qty, estimated_cost, "REJECTED",
                       rejection_reason="User did not confirm")
        console.print("[bright_yellow]Order cancelled.[/bright_yellow]")
        return False


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def log_live_audit(
    ticker: str,
    side: str,
    qty: int,
    estimated_cost: float,
    outcome: str,
    rejection_reason: str = '',
) -> None:
    """Write a row to the live_audit table.

    Called automatically by confirm_live_execution().  Also available for
    direct use by modules that need to log order outcomes after submission
    (e.g., logging "ERROR" when Alpaca rejects the order).

    Parameters:
        ticker:           The ticker symbol
        side:             "BUY" or "SELL"
        qty:              Number of shares/units
        estimated_cost:   Approximate dollar value
        outcome:          One of: "CONFIRMED", "REJECTED", "ERROR",
                          "PAPER_PASSTHROUGH"
        rejection_reason: Why the order was rejected (empty string if
                          confirmed or paper passthrough)
    """
    try:
        from buffet_bot.globals import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT INTO live_audit
               (timestamp, ticker, side, qty, estimated_cost,
                trading_mode, outcome, rejection_reason,
                client_ip, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                ticker.upper(),
                side.upper(),
                int(qty),
                round(float(estimated_cost), 2),
                "LIVE" if is_live_mode() else "PAPER",
                outcome,
                rejection_reason,
                '',  # client_ip — populate if/when multi-session is added
                _SESSION_ID,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # audit logging must never crash the CLI


# ---------------------------------------------------------------------------
# DB schema — live_audit table
# ---------------------------------------------------------------------------

_LIVE_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS live_audit (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    ticker           TEXT    NOT NULL,
    side             TEXT    NOT NULL,
    qty              INTEGER NOT NULL,
    estimated_cost   REAL    NOT NULL DEFAULT 0.0,
    trading_mode     TEXT    NOT NULL,
    outcome          TEXT    NOT NULL,
    rejection_reason TEXT    NOT NULL DEFAULT '',
    client_ip        TEXT    NOT NULL DEFAULT '',
    session_id       TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_live_audit_ticker
    ON live_audit(ticker);

CREATE INDEX IF NOT EXISTS idx_live_audit_timestamp
    ON live_audit(timestamp);
"""


def init_live_audit_table() -> None:
    """Create the live_audit table and indexes if they do not exist.

    Called from db.py init_db().  Uses CREATE TABLE IF NOT EXISTS and
    CREATE INDEX IF NOT EXISTS for backwards compatibility with existing
    databases that do not yet have this table.
    """
    try:
        from buffet_bot.globals import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.executescript(_LIVE_AUDIT_DDL)
        conn.commit()
        conn.close()
    except Exception:
        pass  # schema init must never crash the CLI

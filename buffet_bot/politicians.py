"""
buffet_bot/politicians.py
Congressional trade intelligence — House Stock Watcher S3 feed + FMP API.
"""

from __future__ import annotations

import os
import requests
from datetime import datetime, timedelta

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

# ── Constants ─────────────────────────────────────────────────────────────────

HOUSE_S3_URL = (
    "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com"
    "/data/all_transactions.json"
)
FMP_BASE = "https://financialmodelingprep.com/stable"
FMP_API_KEY = os.getenv("FMP_API_KEY", "")

# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_date(date_str: str) -> datetime | None:
    """Try multiple date formats, return datetime or None."""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(date_str)[:10], fmt[:len(fmt.replace("%H:%M:%S", ""))])
        except Exception:
            pass
    try:
        return datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
    except Exception:
        return None


def _days_ago(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=days)


# ── Data fetchers ─────────────────────────────────────────────────────────────


def fetch_house_trades(ticker: str | None = None, days: int = 90) -> list[dict]:
    """
    Download House Stock Watcher S3 JSON and return normalised trade dicts.
    Optionally filter by ticker and recency window (days).
    """
    try:
        resp = requests.get(HOUSE_S3_URL, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        return []

    cutoff = _days_ago(days)
    results: list[dict] = []

    for item in raw:
        # Filter by ticker if requested
        sym = (item.get("ticker") or "").upper().strip()
        if ticker and sym != ticker.upper():
            continue

        # Skip non-stock entries
        if not sym or sym in ("--", "N/A", ""):
            continue

        # Parse date
        raw_date = item.get("transaction_date") or item.get("disclosure_date") or ""
        dt = _parse_date(raw_date)
        if dt and dt < cutoff:
            continue

        tx_type = item.get("type") or item.get("transaction_type") or ""
        results.append({
            "ticker":   sym,
            "date":     raw_date[:10] if raw_date else "?",
            "name":     item.get("representative") or "Unknown",
            "chamber":  "House",
            "action":   _normalise_action(tx_type),
            "amount":   item.get("amount") or "?",
            "source":   "house_watcher",
        })

    results.sort(key=lambda x: x["date"], reverse=True)
    return results


def fetch_fmp_trades(ticker: str | None = None) -> list[dict]:
    """
    Fetch latest Senate + House trades from Financial Modeling Prep.
    Returns empty list silently if FMP_API_KEY is not set.
    """
    if not FMP_API_KEY:
        return []

    results: list[dict] = []
    for endpoint, chamber in [
        (f"{FMP_BASE}/senate-latest", "Senate"),
        (f"{FMP_BASE}/house-latest", "House"),
    ]:
        try:
            resp = requests.get(
                endpoint,
                params={"apikey": FMP_API_KEY},
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json()
            if not isinstance(items, list):
                continue
        except Exception:
            continue

        for item in items:
            sym = (item.get("symbol") or item.get("ticker") or "").upper().strip()
            if ticker and sym != ticker.upper():
                continue
            if not sym:
                continue

            results.append({
                "ticker":  sym,
                "date":    (item.get("transactionDate") or item.get("date") or "")[:10],
                "name":    item.get("representative") or item.get("senator") or "Unknown",
                "chamber": chamber,
                "action":  _normalise_action(item.get("type") or ""),
                "amount":  item.get("amount") or "?",
                "source":  "fmp",
            })

    results.sort(key=lambda x: x["date"], reverse=True)
    return results


def _normalise_action(raw: str) -> str:
    """Standardise action labels."""
    r = raw.lower()
    if "purchase" in r or "buy" in r:
        return "Purchase"
    if "sale" in r or "sell" in r:
        return "Sale"
    if "exchange" in r:
        return "Exchange"
    return raw.strip() or "Unknown"


def merge_deduplicate(trades_a: list[dict], trades_b: list[dict]) -> list[dict]:
    """Merge two trade lists and remove near-duplicates (same ticker+name+date)."""
    seen: set[tuple] = set()
    merged: list[dict] = []
    for t in trades_a + trades_b:
        key = (t["ticker"], t["name"][:20], t["date"])
        if key not in seen:
            seen.add(key)
            merged.append(t)
    merged.sort(key=lambda x: x["date"], reverse=True)
    return merged


# ── Display ───────────────────────────────────────────────────────────────────


def display_politician_trades(
    trades: list[dict],
    ticker: str,
    console: Console,
) -> None:
    """Render a Rich table of congressional trades for *ticker*."""
    if not trades:
        console.print(
            f"[dim]No congressional trades found for {ticker} "
            "in the requested window.[/dim]"
        )
        return

    tbl = Table(
        title=f"Congressional Trades — {ticker}",
        box=box.ROUNDED,
        header_style="bold blue",
    )
    tbl.add_column("Date",    style="dim", no_wrap=True)
    tbl.add_column("Name",    style="bold")
    tbl.add_column("Chamber", style="dim")
    tbl.add_column("Action")
    tbl.add_column("Amount")

    for t in trades[:30]:
        action = t["action"]
        color = "green" if action == "Purchase" else ("red" if action == "Sale" else "yellow")
        tbl.add_row(
            t["date"],
            t["name"],
            t["chamber"],
            f"[{color}]{action}[/{color}]",
            t["amount"],
        )

    console.print(tbl)

    buys  = sum(1 for t in trades if t["action"] == "Purchase")
    sells = sum(1 for t in trades if t["action"] == "Sale")
    total = len(trades)
    console.print(
        f"[dim]Total: {total} trades  |  "
        f"[green]Purchases: {buys}[/green]  |  "
        f"[red]Sales: {sells}[/red][/dim]\n"
    )

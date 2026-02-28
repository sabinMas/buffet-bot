"""
buffet_bot/volatile.py
High-beta / small-cap / short-interest volatility screener.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console
from rich.table import Table
from rich import box

# ── Universe ──────────────────────────────────────────────────────────────────

VOLATILE_UNIVERSE: list[str] = [
    # Meme / high-retail
    "GME", "AMC", "BBBY", "CLOV", "WISH",
    # EV / clean energy
    "LCID", "RIVN", "WKHS", "GOEV", "FSR",
    # Biotech / speculative
    "SNDL", "MVIS", "SPCE", "NKLA", "HYLN",
    # Fintech / BNPL / neo-finance
    "SOFI", "HOOD", "UPST", "AFRM", "OPEN",
    # Crypto-adjacent equities
    "COIN", "MSTR", "RIOT", "MARA", "HUT",
    # Small-cap tech
    "PLTR", "BBAI", "SOUN", "AI", "IONQ",
    # Mining / commodities speculative
    "MP", "ARNC", "CLF", "VALE", "AA",
    # Cannabis
    "TLRY", "CGC", "CURLF", "APHA", "ACB",
    # SPACs / recent IPOs
    "PSNY", "HLGN", "KPLT", "PAYA", "DKNG",
    # Retail / meme-adjacent
    "BBWI", "EXPR", "GNC", "PRTY", "VIRT",
    # Semiconductors small-cap
    "NVTS", "CEVA", "COHU", "FORM", "AOSL",
    # Healthcare speculative
    "NRXP", "IDEX", "GXII", "MNMD", "ATAI",
    # Short-squeeze candidates (historically)
    "RKT", "UWMC", "HIMS", "BODY", "PRPB",
    # Travel / leisure recovery
    "PTON", "NCLH", "CCL", "AAL", "UAL",
    # Misc high-volatility
    "TSLA", "NFLX", "META", "SNAP", "PINS",
]

# ── Scoring ───────────────────────────────────────────────────────────────────

_MARKET_CAP_THRESHOLD = 2_000_000_000   # $2 B
_SHORT_PCT_THRESHOLD  = 0.10             # 10 %
_BETA_THRESHOLD       = 1.5


def score_ticker_volatility(ticker: str) -> dict | None:
    """
    Compute a 0–100 volatility score for *ticker* using yfinance.
    Returns None on error.
    """
    try:
        t    = yf.Ticker(ticker)
        info = t.info

        def _f(key, fallback=0.0):
            v = info.get(key)
            try:
                return float(v) if v is not None else float(fallback)
            except (TypeError, ValueError):
                return float(fallback)

        beta        = _f("beta")
        market_cap  = _f("marketCap", 0)
        short_pct   = _f("shortPercentOfFloat", 0)   # fraction, e.g. 0.12

        # 30-day daily return std
        try:
            hist    = yf.download(ticker, period="1mo", progress=False, auto_adjust=True)
            close   = hist["Close"].squeeze().astype(float)
            vol_30d = float(close.pct_change().dropna().std()) * 100  # %
        except Exception:
            vol_30d = 0.0

        score = 0

        # Beta > 1.5  → 30 pts (proportional cap at beta 4)
        if beta > 0:
            score += min(30, int(30 * min(beta / _BETA_THRESHOLD, 2) / 2))

        # Market cap < $2 B → 25 pts
        if 0 < market_cap < _MARKET_CAP_THRESHOLD:
            score += 25
        elif market_cap == 0:
            score += 10  # unknown / micro

        # Short % of float > 10% → 25 pts (proportional up to 50%)
        if short_pct > 0:
            score += min(25, int(25 * min(short_pct / _SHORT_PCT_THRESHOLD, 5) / 5))

        # 30-day vol → 20 pts (proportional, capped at 10% daily std)
        if vol_30d > 0:
            score += min(20, int(20 * min(vol_30d / 10.0, 1.0)))

        score = min(100, max(0, score))

        return {
            "ticker":      ticker,
            "beta":        round(beta, 2),
            "market_cap":  market_cap,
            "short_pct":   round(short_pct * 100, 1),
            "vol_30d":     round(vol_30d, 2),
            "score":       score,
        }
    except Exception:
        return None


def scan_volatile(universe: list[str] | None = None, n: int = 10) -> list[dict]:
    """
    Score every ticker in *universe* (default: VOLATILE_UNIVERSE) concurrently.
    Returns the top-*n* by score, descending.
    """
    tickers = universe if universe else VOLATILE_UNIVERSE
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(score_ticker_volatility, t): t for t in tickers}
        for fut in as_completed(futures):
            res = fut.result()
            if res is not None:
                results.append(res)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:n]


# ── Display ───────────────────────────────────────────────────────────────────


def display_volatile_table(results: list[dict], console: Console) -> None:
    """Render a Rich ranked table of volatility scores."""
    if not results:
        console.print("[yellow]No volatile results found.[/yellow]")
        return

    tbl = Table(
        title="Volatile Stock Scanner — Top Picks",
        box=box.ROUNDED,
        header_style="bold magenta",
    )
    tbl.add_column("Rank",     justify="right", style="dim")
    tbl.add_column("Ticker",   style="bold cyan")
    tbl.add_column("Beta",     justify="right")
    tbl.add_column("Mkt Cap",  justify="right")
    tbl.add_column("Short %",  justify="right")
    tbl.add_column("30d Vol%", justify="right")
    tbl.add_column("Score",    justify="right")

    for i, r in enumerate(results, 1):
        score  = r["score"]
        s_color = "red" if score >= 75 else "yellow" if score >= 50 else "green"
        cap    = r["market_cap"]
        if cap >= 1_000_000_000:
            cap_str = f"${cap/1_000_000_000:.1f}B"
        elif cap >= 1_000_000:
            cap_str = f"${cap/1_000_000:.0f}M"
        else:
            cap_str = "—" if cap == 0 else f"${cap:,.0f}"

        tbl.add_row(
            str(i),
            r["ticker"],
            str(r["beta"]) if r["beta"] else "—",
            cap_str,
            f"{r['short_pct']:.1f}%" if r["short_pct"] else "—",
            f"{r['vol_30d']:.1f}%"   if r["vol_30d"]   else "—",
            f"[{s_color}]{score}[/{s_color}]",
        )

    console.print(tbl)

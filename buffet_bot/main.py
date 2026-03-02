import os
import json
import pathlib
import click
import contextlib
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3
import math
import itertools
import numpy as np
from collections import Counter, deque
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockLatestBarRequest
import yfinance as yf
import pandas as pd
import ollama
import time
import warnings
warnings.filterwarnings('ignore')

from buffet_bot.politicians import (
    fetch_house_trades, fetch_fmp_trades, merge_deduplicate, display_politician_trades,
)
from buffet_bot.crypto import (
    CRYPTO_SYMBOLS, is_crypto_symbol,
    get_crypto_bars, get_crypto_quote, get_crypto_volatility,
    init_coinbase, coinbase_market_buy, get_coinbase_balance,
    analyze_crypto as _analyze_crypto,
)
from buffet_bot.volatile import scan_volatile, display_volatile_table, VOLATILE_UNIVERSE
from buffet_bot.ibkr import get_ibkr_status, ibkr_market_order
from buffet_bot.insiders import (
    fetch_insider_transactions, insider_summary,
    insider_prompt_block, display_insider_table,
)
from buffet_bot.universe import (
    list_companies, search_companies, search_edgar, SECTORS,
)
from buffet_bot.automate import run_agent_loop

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.text import Text
from rich import box
import plotext as plt

load_dotenv()

API_KEY = os.getenv('ALPACA_API_KEY')
SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
if not API_KEY or not SECRET_KEY:
    raise ValueError("Add ALPACA_API_KEY and ALPACA_SECRET_KEY to .env")

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
console = Console()

MODELS = ['deepseek-r1', 'qwen2.5:7b']
ALPACA_PAPER_BASE = 'https://paper-api.alpaca.markets'

MODEL_COLORS = {
    'deepseek-r1': 'cyan',
    'qwen2.5:7b': 'magenta',
}

PLANS_DIR   = os.path.expanduser("~/.buffet-plans")
DB_PATH     = os.path.expanduser("~/.buffet-bot.db")
CONFIG_PATH = os.path.expanduser("~/.buffet-bot-config.toml")

def _safe_plan_path(name: str) -> pathlib.Path:
    """Return the resolved Path for a plan file, raising ValueError on path traversal.

    Applies two layers of defence:
    1. Allowlist — only letters, digits, hyphens, and underscores are permitted.
       This blocks '.', '..', slashes, and any other shell-special characters
       before pathlib ever sees them.
    2. Resolved-path check — confirms the final path is still inside PLANS_DIR
       (belt-and-suspenders against any future edge cases).
    """
    # Layer 1: allowlist (strips hyphens/underscores then checks alphanumeric)
    if not name or not name.replace('-', '').replace('_', '').isalnum():
        raise ValueError(f"Invalid plan name: {name!r}")
    plans_dir = pathlib.Path(PLANS_DIR).resolve()
    target = (plans_dir / f"{name}.json").resolve()
    # Layer 2: resolved-path confinement
    if not str(target).startswith(str(plans_dir) + os.sep):
        raise ValueError(f"Invalid plan name: {name!r}")
    return target

FRED_API_KEY = os.getenv('FRED_API_KEY', '')

NASDAQ_EARNINGS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin":     "https://www.nasdaq.com",
    "Referer":    "https://www.nasdaq.com/",
    "Accept":     "application/json, text/plain, */*",
}

STRATEGY_PROMPTS = {
    'value': (
        "Focus on Warren Buffett's classic value principles: high ROE, low debt, "
        "durable competitive moat, P/E and P/B below sector median. "
        "Require a 30%+ margin of safety from intrinsic value. Penalize P/E > 25 or P/B > 5."
    ),
    'growth': (
        "Focus on high-growth characteristics: 5Y earnings CAGR > 15%, expanding margins, "
        "strong revenue momentum. Accept higher P/E if justified by growth rate (PEG < 1.5). "
        "Prioritize total addressable market size and reinvestment rate."
    ),
    'dividend': (
        "Focus on income generation: consistent dividend yield > 2.5%, payout ratio < 60%, "
        "10+ years of dividend growth. Prioritize FCF coverage of dividends and debt stability. "
        "Flag dividend traps where yield > 8% with declining earnings."
    ),
    'turnaround': (
        "Focus on distressed assets with recovery catalysts: recent earnings trajectory improvement, "
        "debt reduction trend, new management, or sector tailwind. Accept recent losses if the "
        "fundamental thesis is clearly improving. High risk — require a strong conviction catalyst."
    ),
}

GOAL_PRESETS = {
    'growth':   ['AAPL', 'MSFT', 'GOOGL', 'NVDA', 'AMZN'],
    'income':   ['JNJ', 'PG', 'KO', 'VZ', 'ABBV'],
    'balanced': ['V', 'JPM', 'BRK-B', 'SPY', 'QQQ'],
    'etf':      ['SPY', 'QQQ', 'VTI', 'SCHD', 'AGG'],
    'buffett':  ['BRK-B', 'KO', 'AAPL', 'JNJ', 'V'],
}

# ── Config ────────────────────────────────────────────────────────────────────

try:
    import tomllib
except ImportError:
    tomllib = None  # Python < 3.11 — config reads silently disabled

try:
    import tomli_w
except ImportError:
    tomli_w = None

_CONFIG_DEFAULTS = {
    'defaults': {'model': MODELS[0], 'risk': 'medium', 'strategy': 'value'},
    'display':  {'buffett_score_green': 70, 'buffett_score_yellow': 40},
}

def _load_config():
    """Load ~/.buffet-bot-config.toml, merging with hardcoded defaults."""
    cfg = {s: dict(v) for s, v in _CONFIG_DEFAULTS.items()}
    if tomllib is None or not os.path.exists(CONFIG_PATH):
        return cfg
    try:
        with open(CONFIG_PATH, 'rb') as f:
            user = tomllib.load(f)
        for section, values in user.items():
            if section in cfg and isinstance(values, dict):
                cfg[section].update(values)
    except Exception:
        pass
    return cfg

_CONFIG = _load_config()

# ── Database ──────────────────────────────────────────────────────────────────

def init_db():
    """Create recommendation/outcome tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT    NOT NULL,
            ticker        TEXT    NOT NULL,
            action        TEXT    NOT NULL,
            confidence    REAL    NOT NULL DEFAULT 0.0,
            qty           INTEGER NOT NULL DEFAULT 0,
            entry_price   REAL    NOT NULL DEFAULT 0.0,
            reason        TEXT    NOT NULL DEFAULT '',
            model         TEXT    NOT NULL DEFAULT '',
            strategy      TEXT    NOT NULL DEFAULT 'value',
            buffett_score INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS outcomes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER NOT NULL REFERENCES recommendations(id),
            exit_timestamp    TEXT    NOT NULL,
            exit_price        REAL    NOT NULL DEFAULT 0.0,
            pnl_pct           REAL    NOT NULL DEFAULT 0.0,
            holding_days      INTEGER NOT NULL DEFAULT 0,
            outcome_note      TEXT    NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS watchlist (
            ticker   TEXT PRIMARY KEY,
            added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker     TEXT    NOT NULL,
            type       TEXT    NOT NULL,
            threshold  REAL    NOT NULL,
            note       TEXT    NOT NULL DEFAULT '',
            created_at TEXT    NOT NULL,
            triggered  INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS earnings_surprises (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker        TEXT    NOT NULL,
            report_date   TEXT    NOT NULL,
            eps_actual    REAL    NOT NULL,
            eps_forecast  REAL    NOT NULL,
            surprise_pct  REAL    NOT NULL,
            beat_miss     TEXT    NOT NULL,
            logged_at     TEXT    NOT NULL,
            UNIQUE(ticker, report_date)
        );
    """)
    conn.commit()
    conn.close()

def log_recommendation(ticker, action, confidence, qty, entry_price,
                       reason, model, strategy, buffett_score):
    """Insert a BUY recommendation row. Silent on any error."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT INTO recommendations
               (timestamp, ticker, action, confidence, qty, entry_price,
                reason, model, strategy, buffett_score)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                ticker, action,
                float(confidence), int(qty), float(entry_price),
                str(reason)[:500],
                model, strategy, int(buffett_score),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

def get_recent_recommendations(days=30):
    """Return list of dicts for recommendations within the last N days."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT * FROM recommendations WHERE timestamp >= ? ORDER BY timestamp DESC",
            (cutoff,),
        ).fetchall()
        cols = [d[0] for d in conn.execute(
            "SELECT * FROM recommendations LIMIT 0"
        ).description]
        conn.close()
        return [dict(zip(cols, row)) for row in rows]
    except Exception:
        return []

def add_to_watchlist(ticker):
    """Add a ticker to the persistent watchlist. Silent if already present."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (ticker, added_at) VALUES (?, ?)",
            (ticker.upper(), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

def remove_from_watchlist(ticker):
    """Remove a ticker from the persistent watchlist."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker.upper(),))
        conn.commit()
        conn.close()
    except Exception:
        pass

def get_watchlist():
    """Return list of tickers in the watchlist, sorted alphabetically."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT ticker, added_at FROM watchlist ORDER BY ticker"
        ).fetchall()
        conn.close()
        return [{'ticker': r[0], 'added_at': r[1][:10]} for r in rows]
    except Exception:
        return []

def create_alert(ticker, alert_type, threshold, note=''):
    """Insert an alert row. Returns the new row id, or None on error."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            "INSERT INTO alerts (ticker, type, threshold, note, created_at) VALUES (?,?,?,?,?)",
            (ticker.upper(), alert_type, float(threshold), note,
             datetime.now(timezone.utc).isoformat()),
        )
        row_id = cur.lastrowid
        conn.commit()
        conn.close()
        return row_id
    except Exception:
        return None

def get_alerts(triggered=False):
    """Return list of alert dicts. triggered=False returns only active alerts."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT id, ticker, type, threshold, note, created_at, triggered "
            "FROM alerts WHERE triggered = ? ORDER BY ticker, type",
            (1 if triggered else 0,),
        ).fetchall()
        conn.close()
        cols = ['id', 'ticker', 'type', 'threshold', 'note', 'created_at', 'triggered']
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []

def delete_alert(alert_id):
    """Delete an alert by id."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM alerts WHERE id = ?", (int(alert_id),))
        conn.commit()
        conn.close()
    except Exception:
        pass

def mark_alert_triggered(alert_id):
    """Mark an alert as triggered (won't appear in future checks)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE alerts SET triggered = 1 WHERE id = ?", (int(alert_id),))
        conn.commit()
        conn.close()
    except Exception:
        pass

def log_earnings_result(ticker: str, report_date: str,
                        eps_actual: float, eps_forecast: float) -> bool:
    """Record an earnings result. Returns True on insert, False on duplicate/error."""
    try:
        surprise_pct = ((eps_actual - eps_forecast) / abs(eps_forecast) * 100
                        if eps_forecast != 0 else 0.0)
        if surprise_pct >= 3:
            beat_miss = 'BEAT'
        elif surprise_pct <= -3:
            beat_miss = 'MISS'
        else:
            beat_miss = 'IN-LINE'
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT OR IGNORE INTO earnings_surprises
               (ticker, report_date, eps_actual, eps_forecast,
                surprise_pct, beat_miss, logged_at)
               VALUES (?,?,?,?,?,?,?)""",
            (ticker.upper(), report_date,
             round(float(eps_actual), 4), round(float(eps_forecast), 4),
             round(surprise_pct, 2), beat_miss,
             datetime.now(timezone.utc).isoformat()),
        )
        inserted = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        conn.close()
        return bool(inserted)
    except Exception:
        return False

def get_earnings_history(ticker: str = '', limit: int = 20) -> list[dict]:
    """Return recent earnings_surprises rows. Pass ticker='' for all tickers."""
    try:
        conn = sqlite3.connect(DB_PATH)
        if ticker:
            rows = conn.execute(
                """SELECT ticker, report_date, eps_actual, eps_forecast,
                          surprise_pct, beat_miss
                   FROM earnings_surprises
                   WHERE ticker = ?
                   ORDER BY report_date DESC LIMIT ?""",
                (ticker.upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT ticker, report_date, eps_actual, eps_forecast,
                          surprise_pct, beat_miss
                   FROM earnings_surprises
                   ORDER BY report_date DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        conn.close()
        cols = ['ticker', 'report_date', 'eps_actual', 'eps_forecast',
                'surprise_pct', 'beat_miss']
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []

init_db()

@click.group()
@click.version_option()
def cli():
    """Buffet-Bot the AI Trading Bot CLI - Local LLM Powered"""
    pass

# ── Data helpers ─────────────────────────────────────────────────────────────

def get_buffett_metrics(ticker):
    """Calculate expanded Buffett value score across 6 criteria (100 pts max)."""
    try:
        t = yf.Ticker(ticker)
        info = t.info

        def _f(key, fallback=0.0):
            v = info.get(key)
            try:
                return float(v) if v is not None else float(fallback)
            except (TypeError, ValueError):
                return float(fallback)

        roe        = _f('returnOnEquity') * 100        # %
        debt_eq    = _f('debtToEquity', 999)            # ratio
        op_margin  = _f('operatingMargins') * 100      # %
        roa        = _f('returnOnAssets') * 100         # ROIC proxy %
        pe         = _f('trailingPE')
        pb         = _f('priceToBook')
        fcf        = _f('freeCashflow')
        mkt_cap    = _f('marketCap', 1)
        div_yield  = _f('dividendYield') * 100          # %
        eg_1y      = _f('earningsGrowth') * 100         # %

        fcf_yield = (fcf / mkt_cap * 100) if mkt_cap > 0 else 0.0

        score = 0
        metrics: dict = {}

        if roe > 15:        score += 25; metrics['roe_pass'] = True
        if roa > 12:        score += 20; metrics['roic_pass'] = True
        if debt_eq < 50:    score += 15; metrics['debt_pass'] = True
        if op_margin > 10:  score += 15; metrics['margin_pass'] = True
        if fcf_yield > 3:   score += 15; metrics['fcf_pass'] = True
        if 0 < pe < 25:     score += 5;  metrics['pe_pass'] = True
        if 0 < pb < 5:      score += 3;  metrics['pb_pass'] = True
        if div_yield > 1.5: score += 2;  metrics['div_pass'] = True

        beta = float(info.get('beta') or 1.0)
        metrics.update({
            'score':        score,
            'roe':          round(roe, 1),
            'roic':         round(roa, 1),
            'debt_eq':      round(debt_eq, 1),
            'op_margin':    round(op_margin, 1),
            'fcf_yield':    round(fcf_yield, 1),
            'pe':           round(pe, 1),
            'pb':           round(pb, 2),
            'div_yield':    round(div_yield, 2),
            'eg_1y':        round(eg_1y, 1),
            'beta':         round(beta, 2),
        })
        return metrics
    except Exception as e:
        console.print(f"[red]Metrics error for {ticker}: {e}[/red]")
        return {'score': 0}

def get_analyst_consensus(ticker: str) -> dict:
    """Fetch Wall Street analyst consensus and price targets via yfinance.

    Returns:
        rating_key   — normalised label: STRONG BUY / BUY / HOLD / UNDERPERFORM / SELL
        rating_mean  — raw 1–5 score (1=strong buy, 5=strong sell)
        target_mean  — consensus 12-month price target
        target_low   — bear-case price target
        target_high  — bull-case price target
        upside_pct   — implied upside from current price to target_mean
        num_analysts — number of analysts covering the stock
        recent_changes — list of last 5 upgrades/downgrades
    Returns {} on any failure (non-blocking).
    """
    _RATING_MAP = {
        'strong_buy':   'STRONG BUY',
        'buy':          'BUY',
        'hold':         'HOLD',
        'underperform': 'UNDERPERFORM',
        'sell':         'SELL',
    }
    try:
        t    = yf.Ticker(ticker)
        info = t.info

        target_mean   = info.get('targetMeanPrice')
        target_low    = info.get('targetLowPrice')
        target_high   = info.get('targetHighPrice')
        current       = info.get('currentPrice') or info.get('regularMarketPrice')
        num_analysts  = info.get('numberOfAnalystOpinions')
        rating_raw    = info.get('recommendationMean')
        rating_key_in = (info.get('recommendationKey') or '').lower().replace(' ', '_')
        rating_key    = _RATING_MAP.get(rating_key_in, rating_key_in.upper() or 'N/A')

        if not target_mean:
            return {}

        upside_pct = round((float(target_mean) - float(current)) / float(current) * 100, 1) \
                     if target_mean and current else None

        recent_changes: list[dict] = []
        try:
            ud = t.upgrades_downgrades
            if ud is not None and not ud.empty:
                for _, row in ud.head(5).iterrows():
                    recent_changes.append({
                        'firm':   str(row.get('Firm', '')),
                        'from':   str(row.get('FromGrade', '')),
                        'to':     str(row.get('ToGrade', '')),
                        'action': str(row.get('Action', '')),
                    })
        except Exception:
            pass

        return {
            'rating_key':      rating_key,
            'rating_mean':     round(float(rating_raw), 2) if rating_raw else None,
            'target_mean':     round(float(target_mean), 2),
            'target_low':      round(float(target_low),  2) if target_low  else None,
            'target_high':     round(float(target_high), 2) if target_high else None,
            'current_price':   round(float(current),     2) if current     else None,
            'upside_pct':      upside_pct,
            'num_analysts':    int(num_analysts) if num_analysts else None,
            'recent_changes':  recent_changes,
        }
    except Exception:
        return {}


def get_tech_indicators(ticker):
    """Basic RSI/MACD for high-risk"""
    data = yf.download(ticker, period='3mo', progress=False)
    if data.empty:
        return {}
    delta = data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1]
    ema12 = data['Close'].ewm(span=12).mean()
    ema26 = data['Close'].ewm(span=26).mean()
    macd = ema12 - ema26
    return {'rsi': round(rsi, 1), 'macd': round(macd.iloc[-1], 4)}

def get_realtime_data(ticker):
    """Fetch current price and today's OHLCV bar from Alpaca Data API."""
    try:
        quote_req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        bar_req = StockLatestBarRequest(symbol_or_symbols=ticker)
        quote = data_client.get_stock_latest_quote(quote_req)[ticker]
        bar = data_client.get_stock_latest_bar(bar_req)[ticker]
        mid = round((float(quote.ask_price) + float(quote.bid_price)) / 2, 2)
        open_price = float(bar.open)
        change_pct = round((mid - open_price) / open_price * 100, 2) if open_price else 0
        return {
            'price': mid,
            'open': open_price,
            'high': float(bar.high),
            'low': float(bar.low),
            'volume': int(bar.volume),
            'change_pct': change_pct,
            'source': 'alpaca',
        }
    except Exception:
        pass
    # Fallback to yfinance
    try:
        fi = yf.Ticker(ticker).fast_info
        price = float(fi.last_price)
        open_price = float(fi.open) if fi.open else price
        change_pct = round((price - open_price) / open_price * 100, 2) if open_price else 0
        return {
            'price': price,
            'open': open_price,
            'high': float(fi.day_high) if fi.day_high else price,
            'low': float(fi.day_low) if fi.day_low else price,
            'volume': int(fi.three_month_average_volume) if fi.three_month_average_volume else 0,
            'change_pct': change_pct,
            'source': 'yfinance',
        }
    except Exception as e:
        console.print(f"[dim red]Live quote unavailable for {ticker}: {e}[/dim red]")
        return {}

def _fetch_fred_data() -> dict:
    """Fetch latest Fed rate, yield curve, and CPI from FRED (3 concurrent requests)."""
    if not FRED_API_KEY:
        return {}
    series = {'DFF': 'fed_rate', 'T10Y2Y': 'yield_curve', 'CPIAUCSL': 'cpi'}

    def _fetch_one(series_id, key):
        try:
            r = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    'series_id': series_id,
                    'api_key':   FRED_API_KEY,
                    'file_type': 'json',
                    'sort_order': 'desc',
                    'limit': 1,
                },
                timeout=5,
            )
            r.raise_for_status()
            val = r.json()['observations'][0]['value']
            return key, float(val)
        except Exception:
            return key, None

    result = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = [ex.submit(_fetch_one, sid, key) for sid, key in series.items()]
        for f in as_completed(futs):
            k, v = f.result()
            if v is not None:
                result[k] = v
    return result

def _get_earnings_date(ticker: str) -> dict | None:
    """Check Nasdaq earnings calendar for the next 7 days. Returns dict or None."""
    for days_ahead in range(8):
        date_str = (datetime.utcnow() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
        try:
            r = requests.get(
                "https://api.nasdaq.com/api/calendar/earnings",
                params={"date": date_str},
                headers=NASDAQ_EARNINGS_HEADERS,
                timeout=5,
            )
            r.raise_for_status()
            rows = r.json().get('data', {}).get('rows') or []
            for row in rows:
                if row.get('symbol', '').upper() == ticker.upper():
                    return {
                        'date':           date_str,
                        'time':           row.get('time', ''),
                        'eps_forecast':   row.get('epsForecast', ''),
                        'fiscal_quarter': row.get('fiscalQuarterEnding', ''),
                    }
        except Exception:
            pass
        time.sleep(0.05)
    return None

def get_recent_news(ticker, limit=5):
    """Fetch recent news headlines from Alpaca News API."""
    try:
        url = "https://data.alpaca.markets/v1beta1/news"
        headers = {
            'APCA-API-KEY-ID': API_KEY,
            'APCA-API-SECRET-KEY': SECRET_KEY,
        }
        params = {'symbols': ticker, 'limit': limit, 'sort': 'desc'}
        resp = requests.get(url, headers=headers, params=params, timeout=5)
        resp.raise_for_status()
        return [
            {
                'headline': item.get('headline', ''),
                'summary': item.get('summary', ''),
                'published_at': item.get('updated_at', '')[:10],
            }
            for item in resp.json().get('news', [])
        ]
    except Exception:
        return []

def analyze_news_sentiment(news_items, ticker, primary_model):
    """Score news headlines with the LLM. Returns aggregate sentiment dict."""
    _neutral = {'overall': 'neutral', 'score': 0.0, 'count': 0, 'items': []}
    if not news_items:
        return _neutral

    headlines = "\n".join(
        f"{i+1}. [{item['published_at']}] {item['headline']}"
        for i, item in enumerate(news_items[:10])
    )
    prompt = (
        f"Analyze the sentiment of each headline for {ticker}. "
        "Return ONLY valid JSON with no extra text:\n"
        '{"items": [{"headline": "...", "sentiment": "positive|neutral|negative", '
        '"confidence": 0.85, "impact": "high|medium|low"}], '
        '"overall_sentiment": "positive|neutral|negative", "sentiment_score": 0.65}\n\n'
        f"Headlines:\n{headlines}"
    )
    try:
        color = MODEL_COLORS.get(primary_model, 'white')
        with console.status(f"[{color}]Analyzing news sentiment ({primary_model})...[/{color}]"):
            resp = ollama.chat(
                model=primary_model,
                messages=[{'role': 'user', 'content': prompt}],
                options={'temperature': 0.1},
            )
        content = resp['message']['content'].strip()
        start, end = content.find('{'), content.rfind('}') + 1
        if start < 0 or end <= start:
            return _neutral
        data = json.loads(content[start:end])
        raw_score = float(data.get('sentiment_score', 0.0))
        return {
            'overall': data.get('overall_sentiment', 'neutral'),
            'score':   max(-1.0, min(1.0, raw_score)),
            'count':   len(news_items),
            'items':   data.get('items', []),
        }
    except Exception:
        return _neutral

# ── Shell completion ─────────────────────────────────────────────────────────

from click.shell_completion import CompletionItem

# All tickers from the bundled universe DB (~500) + crypto pairs for tab completion.
# universe.py is the single source of truth for stock tickers.
from buffet_bot.universe import _COMPANY_DB as _UNIVERSE_DB  # internal — kept here for perf
_COMMON_TICKERS = (
    list(_UNIVERSE_DB.keys())
    + ['BTC/USD', 'ETH/USD', 'SOL/USD', 'DOGE/USD', 'ADA/USD']
)

def _complete_ticker(ctx, param, incomplete):
    """Return watchlist tickers + common tickers for shell tab completion."""
    try:
        watchlist = [row['ticker'] for row in get_watchlist()]
    except Exception:
        watchlist = []
    candidates = list(dict.fromkeys(watchlist + _COMMON_TICKERS))
    return [CompletionItem(t) for t in candidates if t.upper().startswith(incomplete.upper())]

# ── LLM helpers ──────────────────────────────────────────────────────────────

def _query_llms_freeform(prompt_text, primary_model):
    """Query both LLMs with a plain-text prompt, returning raw text responses."""
    models_to_query = [primary_model]
    if primary_model != MODELS[1]:
        models_to_query.append(MODELS[1])

    responses = {}
    for model in models_to_query:
        color = MODEL_COLORS.get(model, 'white')
        with console.status(f"[{color}]Querying {model}...[/{color}]"):
            try:
                resp = ollama.chat(
                    model=model,
                    messages=[{'role': 'user', 'content': prompt_text}],
                    options={'temperature': 0.5},
                )
                responses[model] = resp['message']['content'].strip()
            except Exception as e:
                responses[model] = f"Error: {e}"
    return responses

def _run_analysis(ticker, risk, primary_model, strategy='value'):
    """Fetch data, query LLMs, compute consensus. Returns analysis dict."""
    # ── Concurrent I/O fetch ─────────────────────────────────────────────────
    def _hist_dl():
        return yf.download(ticker, period='6mo', progress=False)['Close'].tail(30)

    with ThreadPoolExecutor(max_workers=9) as ex:
        f_hist       = ex.submit(_hist_dl)
        f_buffett    = ex.submit(get_buffett_metrics, ticker)
        f_realtime   = ex.submit(get_realtime_data, ticker)
        f_news       = ex.submit(get_recent_news, ticker)
        f_macro      = ex.submit(_fetch_fred_data)
        f_insiders   = ex.submit(fetch_insider_transactions, ticker, 60, 5, 5)
        f_multiframe = ex.submit(get_multiframe_signals, ticker)
        f_analyst    = ex.submit(get_analyst_consensus, ticker)
        f_tech       = ex.submit(get_tech_indicators, ticker) if risk == 'high' else None

    hist       = f_hist.result()
    buffett    = f_buffett.result()
    tech       = f_tech.result() if f_tech else {}
    realtime   = f_realtime.result()
    news       = f_news.result()
    macro      = f_macro.result()
    multiframe = f_multiframe.result()
    analyst    = f_analyst.result()
    try:
        insider_txns = f_insiders.result()
    except Exception:
        insider_txns = []

    # Sentiment depends on news — LLM call after concurrent fetch
    sentiment = analyze_news_sentiment(news, ticker, primary_model)

    # Live market block
    if realtime:
        sign = '+' if realtime['change_pct'] >= 0 else ''
        live_block = (
            f"LIVE Market Data (as of now):\n"
            f"  Price: ${realtime['price']:.2f}  |  "
            f"Today: {sign}{realtime['change_pct']}%  |  "
            f"Open: ${realtime['open']:.2f}  |  "
            f"High: ${realtime['high']:.2f}  |  "
            f"Low: ${realtime['low']:.2f}  |  "
            f"Volume: {realtime['volume']:,}"
        )
    else:
        live_block = "LIVE Market Data: unavailable"

    news_block = ""
    if news:
        news_lines = "\n".join(
            f"  {i+1}. [{item['published_at']}] {item['headline']}"
            for i, item in enumerate(news)
        )
        s_sign = '+' if sentiment['score'] >= 0 else ''
        news_block = (
            f"\nRecent News (use for sentiment context):\n{news_lines}\n"
            f"  News Sentiment: {sentiment['overall'].upper()} "
            f"(score: {s_sign}{sentiment['score']:.2f})"
        )

    macro_block = ""
    if macro:
        macro_block = (
            f"\nMacro context: Fed rate {macro.get('fed_rate', 'N/A')}%, "
            f"yield curve (10Y-2Y) {macro.get('yield_curve', 'N/A')}%, "
            f"CPI index {macro.get('cpi', 'N/A')}."
        )

    insiders_block = insider_prompt_block(insider_txns)

    multiframe_block = ""
    if multiframe:
        sma_pos = ('above' if multiframe.get('above_sma50') else
                   'below' if multiframe.get('above_sma50') is False else 'N/A')
        multiframe_block = (
            f"\nMulti-Timeframe Signals: "
            f"RSI(1d)={multiframe.get('rsi_1d', 'N/A')}, "
            f"RSI(1w)={multiframe.get('rsi_1w', 'N/A')}, "
            f"Monthly trend={multiframe.get('trend_1mo', 'N/A')}, "
            f"Price {sma_pos} 50-day SMA."
        )

    analyst_block = ""
    if analyst:
        sign = '+' if (analyst.get('upside_pct') or 0) >= 0 else ''
        analyst_block = (
            f"\nWall Street Analysts ({analyst.get('num_analysts', '?')} covering): "
            f"Consensus={analyst.get('rating_key', 'N/A')}, "
            f"Target=${analyst.get('target_mean', 'N/A')} "
            f"(range ${analyst.get('target_low', '?')}–${analyst.get('target_high', '?')}), "
            f"Implied upside: {sign}{analyst.get('upside_pct', 'N/A')}%."
        )

    strategy_guidance = STRATEGY_PROMPTS.get(strategy, STRATEGY_PROMPTS['value'])

    prompt = f"""
    Buffett Trading AI for {ticker} | Risk: {risk} | Strategy: {strategy.upper()}
    Buffett Score: {buffett['score']}/100 | ROE: {buffett.get('roe','?')}% | ROIC: {buffett.get('roic','?')}% | Debt/Eq: {buffett.get('debt_eq','?')} | OpMargin: {buffett.get('op_margin','?')}% | FCF Yield: {buffett.get('fcf_yield','?')}% | P/E: {buffett.get('pe','?')} | P/B: {buffett.get('pb','?')}
    {live_block}{news_block}{macro_block}{insiders_block}{multiframe_block}{analyst_block}
    Recent Prices (30d): {hist.to_dict()}
    Tech {'(RSI: ' + str(tech.get('rsi', 'N/A')) + ', MACD: ' + str(tech.get('macd', 'N/A')) + ')' if tech else ''}

    Strategy Guidance: {strategy_guidance}

    Additional rules:
    - Risk mgmt: Position <2% portfolio, stop-loss 3-7%
    - Factor in the LIVE price, news sentiment, and macro context above

    JSON only: {{"action": "BUY|SELL|HOLD", "confidence": 0.85, "qty": 10, "reason": "2 sentences", "stop_pct": 0.05}}
    """

    models_to_query = [primary_model]
    if primary_model != MODELS[1]:
        models_to_query.append(MODELS[1])

    responses = {}
    for model in models_to_query:
        color = MODEL_COLORS.get(model, 'white')
        with console.status(f"[{color}]Querying {model}...[/{color}]"):
            try:
                resp = ollama.chat(model=model, messages=[{'role': 'user', 'content': prompt}],
                                   options={'temperature': 0.2})
                advice_str = resp['message']['content'].strip()
                advice = json.loads(advice_str) if advice_str.startswith('{') else {'reason': advice_str}
                responses[model] = advice
            except json.JSONDecodeError:
                responses[model] = {'error': 'Invalid JSON', 'raw': resp['message']['content']}
            except Exception as e:
                responses[model] = {'error': str(e)}

    actions = [r.get('action', 'HOLD') for r in responses.values() if isinstance(r, dict) and 'action' in r]
    consensus = max(set(actions), key=actions.count) if actions else 'HOLD'

    best_buy_resp = None
    if consensus == 'BUY':
        best_buy_resp = max(
            (r for r in responses.values() if isinstance(r, dict) and r.get('action') == 'BUY'),
            key=lambda x: x.get('confidence', 0),
            default=None,
        )
        log_recommendation(
            ticker=ticker,
            action='BUY',
            confidence=best_buy_resp.get('confidence', 0.0),
            qty=best_buy_resp.get('qty', 0),
            entry_price=realtime.get('price', 0.0),
            reason=best_buy_resp.get('reason', ''),
            model=primary_model,
            strategy=strategy,
            buffett_score=buffett.get('score', 0),
        )

    return {
        'buffett': buffett,
        'tech': tech,
        'realtime': realtime,
        'news': news,
        'sentiment': sentiment,
        'responses': responses,
        'consensus': consensus,
        'best_buy_resp': best_buy_resp,
        'analyst': analyst,
    }

def _place_order(ticker, best_resp):
    """Submit a paper market BUY order using the best AI response."""
    try:
        order = MarketOrderRequest(
            symbol=ticker,
            qty=int(best_resp.get('qty', 1)),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        result = trading_client.submit_order(order)
        console.print(f"[bold green]Order submitted:[/bold green] {result.id}")
    except Exception as e:
        console.print(f"[red]Order error: {e}[/red]")

def _print_ai_responses(responses):
    """Print each model's response in a colored panel."""
    for model, resp in responses.items():
        color = MODEL_COLORS.get(model, 'white')
        content = json.dumps(resp, indent=2) if isinstance(resp, dict) else str(resp)
        console.print(Panel(content, title=f"[bold {color}]{model}[/bold {color}]",
                            border_style=color))

def _consensus_text(consensus):
    color = {'BUY': 'green', 'SELL': 'red', 'HOLD': 'yellow'}.get(consensus, 'white')
    return f"[bold {color}]{consensus}[/bold {color}]"

def _score_color(score):
    """Return Rich color for a Buffett score, using config thresholds."""
    disp = _CONFIG.get('display', {})
    green_thresh  = disp.get('buffett_score_green',  70)
    yellow_thresh = disp.get('buffett_score_yellow', 40)
    if score >= green_thresh:
        return 'green'
    if score >= yellow_thresh:
        return 'yellow'
    return 'red'

def _change_color(chg):
    """4-tier color for a price change percentage value."""
    if chg >= 2.0:   return 'bold bright_green'
    if chg >= 0:     return 'green'
    if chg >= -2.0:  return 'red'
    return 'bold bright_red'

def _print_live_market(ticker, realtime, news):
    """Print a live market data panel and recent news table."""
    if realtime:
        sign = '+' if realtime['change_pct'] >= 0 else ''
        pct_color = 'green' if realtime['change_pct'] >= 0 else 'red'
        src = f"[dim] (via {realtime['source']})[/dim]"
        content = (
            f"Price:  [bold]${realtime['price']:.2f}[/bold]  "
            f"[{pct_color}]{sign}{realtime['change_pct']}% today[/{pct_color}]{src}\n"
            f"Open: ${realtime['open']:.2f}  "
            f"High: ${realtime['high']:.2f}  "
            f"Low: ${realtime['low']:.2f}  "
            f"Vol: {realtime['volume']:,}"
        )
        console.print(Panel(content, title=f"[bold]{ticker}[/bold] Live Market", border_style="green"))
    else:
        console.print(f"[dim]Live market data unavailable for {ticker}.[/dim]")

    if news:
        table = Table(title="Recent News", box=box.SIMPLE, header_style="bold dim")
        table.add_column("Date", style="dim", no_wrap=True)
        table.add_column("Headline")
        for item in news:
            table.add_row(item['published_at'], item['headline'])
        console.print(table)

# ── Plan management ──────────────────────────────────────────────────────────

def _ensure_plans_dir():
    os.makedirs(PLANS_DIR, exist_ok=True)

def _save_plan(name, plan_data):
    _ensure_plans_dir()
    path = _safe_plan_path(name)
    plan_data['name'] = name
    plan_data['updated_at'] = datetime.now().isoformat()
    with open(path, 'w') as f:
        json.dump(plan_data, f, indent=2, default=str)
    return str(path)

def _load_plan(name):
    try:
        path = _safe_plan_path(name)
    except ValueError:
        return None
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)

def _list_plans():
    _ensure_plans_dir()
    result = []
    for fname in sorted(os.listdir(PLANS_DIR)):
        if fname.endswith('.json'):
            try:
                with open(os.path.join(PLANS_DIR, fname)) as f:
                    result.append(json.load(f))
            except Exception:
                pass
    return result

# ── Plan scheduler ────────────────────────────────────────────────────────────

_SCHEDULE_DAYS = {
    'daily':    1,
    'weekly':   7,
    'biweekly': 14,
    'monthly':  30,
}

def _is_plan_due(plan: dict) -> bool:
    """Return True if this plan has a schedule and is due to run."""
    schedule = plan.get('schedule')
    if not schedule or schedule not in _SCHEDULE_DAYS:
        return False
    interval = timedelta(days=_SCHEDULE_DAYS[schedule])
    last_run = plan.get('last_run_at')
    if not last_run:
        return True
    try:
        last_dt = datetime.fromisoformat(last_run)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= last_dt + interval
    except Exception:
        return True

def _set_plan_schedule(name: str, schedule: str | None) -> bool:
    """Update a plan's schedule field. Returns True on success."""
    plan = _load_plan(name)
    if plan is None:
        return False
    plan['schedule'] = schedule
    _save_plan(name, plan)
    return True

def _mark_plan_ran(name: str) -> None:
    """Stamp last_run_at = now on a plan after a successful scheduled run."""
    plan = _load_plan(name)
    if plan is not None:
        plan['last_run_at'] = datetime.now(timezone.utc).isoformat()
        _save_plan(name, plan)

def _analyze_portfolio(tickers, budget, risk, primary_model):
    """Analyze a list of tickers and print a summary table. Returns (results, buy_candidates)."""
    results = {}
    for ticker in tickers:
        with console.status(f"[bold blue]Analyzing {ticker}...[/bold blue]"):
            results[ticker] = _run_analysis(ticker, risk, primary_model)

    allocation = budget / len(tickers) if tickers else 0

    table = Table(title="Investment Plan Summary", box=box.ROUNDED, header_style="bold blue")
    table.add_column("Ticker", style="bold cyan")
    table.add_column("Price", justify="right")
    table.add_column("Buffett Score", justify="right")
    table.add_column("Consensus")
    table.add_column("Confidence", justify="right")
    table.add_column("Qty", justify="right")
    table.add_column("Alloc Value", justify="right")

    buy_candidates = []
    for ticker, result in results.items():
        price = result['realtime'].get('price', 0)
        score = result['buffett']['score']
        consensus = result['consensus']
        c_color = {'BUY': 'green', 'SELL': 'red', 'HOLD': 'yellow'}.get(consensus, 'white')
        s_color = _score_color(score)

        qty = 0
        confidence = '—'
        alloc_val = '—'

        if consensus == 'BUY' and result['best_buy_resp']:
            best = result['best_buy_resp']
            confidence = f"{best.get('confidence', 0):.0%}"
            if price > 0:
                qty = max(1, int(allocation / price))
                alloc_val = f"${qty * price:,.2f}"
            else:
                qty = int(best.get('qty', 1))
            buy_candidates.append((ticker, result, qty))

        table.add_row(
            ticker,
            f"${price:.2f}" if price else "—",
            f"[{s_color}]{score}[/{s_color}]",
            f"[{c_color}]{consensus}[/{c_color}]",
            confidence,
            str(qty) if qty else "—",
            alloc_val,
        )

    console.print(table)
    return results, buy_candidates

def _execute_plan_buys(buy_candidates):
    """Prompt the user and place paper orders for each BUY candidate."""
    if not buy_candidates:
        console.print("[yellow]No BUY signals — no orders placed.[/yellow]")
        return

    tickers_str = ", ".join(t for t, _, _ in buy_candidates)
    console.print(f"\n[bold]BUY signals:[/bold] {tickers_str}")

    choice = Prompt.ask(
        "Execute orders? [bold]all[/bold] / [bold]pick[/bold] / [bold]skip[/bold]",
        choices=['all', 'pick', 'skip'],
        default='skip',
    )
    if choice == 'skip':
        return

    for ticker, result, qty in buy_candidates:
        if choice == 'pick' and not click.confirm(f"  Execute BUY {qty}x {ticker}? (Paper)"):
            continue
        best = dict(result['best_buy_resp']) if result['best_buy_resp'] else {}
        best['qty'] = qty
        _place_order(ticker, best)

# ── Guide wizard helpers ──────────────────────────────────────────────────────

def _guide_single_stock(primary_model):
    """Guided single-stock analysis + optional buy."""
    console.print(Panel("[bold]Single Stock Analysis[/bold]", border_style="cyan"))

    raw = Prompt.ask("Ticker symbol or company name").strip()

    # If it looks like a search query rather than a ticker, do a lookup first
    ticker = raw.upper()
    if ' ' in raw or len(raw) > 6 or not raw.replace('-', '').replace('.', '').isalpha():
        console.print(f"[dim]Searching for '{raw}'...[/dim]")
        try:
            results = yf.Search(raw).quotes
            if results:
                tbl = Table(box=box.SIMPLE, header_style="bold dim")
                tbl.add_column("#")
                tbl.add_column("Symbol", style="bold cyan")
                tbl.add_column("Name")
                for i, q in enumerate(results[:5], 1):
                    tbl.add_row(str(i), q.get('symbol', ''), q.get('longname') or q.get('shortname', ''))
                console.print(tbl)
                pick = Prompt.ask("Enter number or type a ticker directly", default="1")
                if pick.isdigit() and 1 <= int(pick) <= len(results[:5]):
                    ticker = results[int(pick) - 1].get('symbol', ticker).upper()
                else:
                    ticker = pick.upper().strip()
        except Exception:
            pass

    risk = Prompt.ask("Risk level", choices=['low', 'medium', 'high'], default='medium')

    console.print(f"\n[dim]Running full analysis on {ticker}...[/dim]")
    result = _run_analysis(ticker, risk, primary_model)
    _print_live_market(ticker, result['realtime'], result['news'])
    _print_ai_responses(result['responses'])
    console.print(f"\nConsensus: {_consensus_text(result['consensus'])}")

    if result['consensus'] == 'BUY' and result['best_buy_resp']:
        if click.confirm(f"\nExecute BUY {ticker}? (Paper)"):
            _place_order(ticker, result['best_buy_resp'])
    else:
        console.print(f"[yellow]Consensus is {result['consensus']} — no trade recommended.[/yellow]")

def _guide_build_plan(primary_model):
    """Guide the user through building and optionally saving a multi-stock plan."""
    console.print(Panel("[bold]Multi-Stock Investment Plan Builder[/bold]", border_style="cyan"))

    console.print("\n[bold]Choose an investment goal:[/bold]")
    console.print("  [bold cyan]1[/bold cyan]  Growth          (AAPL, MSFT, GOOGL, NVDA, AMZN)")
    console.print("  [bold cyan]2[/bold cyan]  Income/Dividend (JNJ, PG, KO, VZ, ABBV)")
    console.print("  [bold cyan]3[/bold cyan]  Balanced        (V, JPM, BRK-B, SPY, QQQ)")
    console.print("  [bold cyan]4[/bold cyan]  ETF-only        (SPY, QQQ, VTI, SCHD, AGG)")
    console.print("  [bold cyan]5[/bold cyan]  Buffett-style   (BRK-B, KO, AAPL, JNJ, V)")
    console.print("  [bold cyan]6[/bold cyan]  Custom          (enter your own tickers)\n")

    goal_choice = Prompt.ask("Goal", choices=['1', '2', '3', '4', '5', '6'], default='1')
    goal_map = {'1': 'growth', '2': 'income', '3': 'balanced', '4': 'etf', '5': 'buffett', '6': 'custom'}
    goal = goal_map[goal_choice]

    if goal == 'custom':
        raw = Prompt.ask("Enter tickers separated by commas (e.g. AAPL, TSLA, SPY)")
        tickers = [t.strip().upper() for t in raw.split(',') if t.strip()]
    else:
        tickers = list(GOAL_PRESETS[goal])
        console.print(f"[dim]Preset tickers: {', '.join(tickers)}[/dim]")
        if click.confirm("Customize the ticker list?", default=False):
            raw_add = Prompt.ask("Add tickers (comma-separated, or leave blank)", default="")
            raw_rem = Prompt.ask("Remove tickers (comma-separated, or leave blank)", default="")
            adds = [t.strip().upper() for t in raw_add.split(',') if t.strip()]
            removes = [t.strip().upper() for t in raw_rem.split(',') if t.strip()]
            tickers = [t for t in tickers + adds if t not in removes]

    if not tickers:
        console.print("[red]No tickers selected.[/red]")
        return

    budget_str = Prompt.ask("Total investment budget ($)", default="5000")
    try:
        budget = float(budget_str.replace('$', '').replace(',', ''))
    except ValueError:
        console.print("[red]Invalid budget.[/red]")
        return

    risk = Prompt.ask("Risk level", choices=['low', 'medium', 'high'], default='medium')

    console.print(f"\n[dim]Analyzing {len(tickers)} tickers with ${budget:,.2f} budget...[/dim]\n")
    _, buy_candidates = _analyze_portfolio(tickers, budget, risk, primary_model)

    plan_data = {
        'tickers': tickers,
        'budget': budget,
        'risk': risk,
        'goal': goal,
        'created_at': datetime.now().isoformat(),
    }

    if click.confirm("\nSave as a recurring investment plan?", default=False):
        plan_name = Prompt.ask("Plan name", default=f"{goal}-plan")
        try:
            path = _save_plan(plan_name, plan_data)
            console.print(f"[green]Plan saved: {path}[/green]")
            console.print(f"[dim]Re-run any time: buffet-bot guide --plan {plan_name}[/dim]")
        except ValueError as e:
            console.print(f"[red]Could not save plan: {e}[/red]")

    _execute_plan_buys(buy_candidates)

def _guide_load_plan(primary_model):
    """Let the user pick a saved plan and execute it with fresh data."""
    saved = _list_plans()
    if not saved:
        console.print("[yellow]No saved plans found. Build one first with option 2.[/yellow]")
        return

    console.print(Panel("[bold]Saved Investment Plans[/bold]", border_style="cyan"))
    tbl = Table(box=box.SIMPLE, header_style="bold dim")
    tbl.add_column("#")
    tbl.add_column("Name", style="bold cyan")
    tbl.add_column("Goal")
    tbl.add_column("Tickers")
    tbl.add_column("Budget", justify="right")
    tbl.add_column("Saved", style="dim")
    for i, p in enumerate(saved, 1):
        tbl.add_row(
            str(i),
            p.get('name', '?'),
            p.get('goal', 'custom'),
            ', '.join(p.get('tickers', [])),
            f"${p.get('budget', 0):,.2f}",
            (p.get('updated_at') or p.get('created_at', ''))[:10],
        )
    console.print(tbl)

    pick = Prompt.ask("Enter plan number", default="1")
    try:
        plan_data = saved[int(pick) - 1]
    except (ValueError, IndexError):
        console.print("[red]Invalid selection.[/red]")
        return

    _run_guide_plan(plan_data, primary_model)

def _run_guide_plan(plan_data, primary_model):
    """Re-analyze and optionally execute a loaded plan with fresh market data."""
    name = plan_data.get('name', 'unnamed')
    tickers = plan_data.get('tickers', [])
    budget = plan_data.get('budget', 5000)
    risk = plan_data.get('risk', 'medium')

    console.print(Panel(
        f"[bold]Plan: {name}[/bold]\n"
        f"Goal: {plan_data.get('goal', 'custom')}  |  "
        f"Budget: ${budget:,.2f}  |  Risk: {risk}\n"
        f"Tickers: {', '.join(tickers)}",
        border_style="cyan",
    ))

    _, buy_candidates = _analyze_portfolio(tickers, budget, risk, primary_model)
    _execute_plan_buys(buy_candidates)

# ── Projections ───────────────────────────────────────────────────────────────

def _calculate_future_value(principal, monthly_contribution, annual_return, years):
    """Monthly-compounding FV formula. annual_return is a decimal (e.g. 0.09)."""
    if years <= 0:
        return float(principal)
    if annual_return == 0:
        return float(principal) + float(monthly_contribution) * years * 12
    r = annual_return / 12
    n = years * 12
    fv_p = float(principal) * (1 + r) ** n
    fv_c = float(monthly_contribution) * (((1 + r) ** n - 1) / r) if monthly_contribution else 0.0
    return fv_p + fv_c

def _get_ai_expected_return(ticker, primary_model):
    """Ask the LLM for a 5-year expected annual return estimate for ticker."""
    prompt = (
        f"Based on {ticker}'s fundamentals, competitive position, and sector trends, "
        "estimate the expected annualized total return over the next 5 years. "
        "Return ONLY valid JSON — no extra text:\n"
        '{"expected_annual_return_pct": 9.5, "volatility_pct": 18.0, "rationale": "brief"}'
    )
    try:
        resp = ollama.chat(
            model=primary_model,
            messages=[{'role': 'user', 'content': prompt}],
            options={'temperature': 0.3},
        )
        content = resp['message']['content'].strip()
        start, end = content.find('{'), content.rfind('}') + 1
        data = json.loads(content[start:end])
        ret = max(-20.0, min(40.0, float(data.get('expected_annual_return_pct', 7.0))))
        vol = max(5.0,  min(60.0, float(data.get('volatility_pct', 20.0))))
        return {'expected_annual_return_pct': ret, 'volatility_pct': vol,
                'rationale': str(data.get('rationale', ''))[:200]}
    except Exception:
        return {'expected_annual_return_pct': 7.0, 'volatility_pct': 20.0,
                'rationale': 'Default conservative estimate'}

def _get_portfolio_expected_return(positions, primary_model):
    """Weighted-average AI return estimate across all current positions."""
    if not positions:
        return {'weighted_return': 0.07, 'weighted_volatility': 0.20, 'per_ticker': {}}
    total_value = sum(float(p.market_value) for p in positions)
    if total_value == 0:
        return {'weighted_return': 0.07, 'weighted_volatility': 0.20, 'per_ticker': {}}
    per_ticker = {}
    for pos in positions:
        weight = float(pos.market_value) / total_value
        with console.status(f"[dim]AI estimating return for {pos.symbol}...[/dim]"):
            est = _get_ai_expected_return(pos.symbol, primary_model)
        per_ticker[pos.symbol] = {'weight': weight, **est}
    weighted_return = sum(
        d['weight'] * d['expected_annual_return_pct'] / 100 for d in per_ticker.values()
    )
    weighted_vol = sum(
        d['weight'] * d['volatility_pct'] / 100 for d in per_ticker.values()
    )
    return {'weighted_return': weighted_return, 'weighted_volatility': weighted_vol,
            'per_ticker': per_ticker}

def _run_monte_carlo(base_return, volatility, balance, monthly, years, n=1000):
    """Simulate n portfolio paths. Returns percentile dict (all dollar values)."""
    months = years * 12
    monthly_ret = base_return / 12
    monthly_vol = volatility / (12 ** 0.5)
    rng = np.random.default_rng()
    shocks = rng.normal(loc=monthly_ret, scale=monthly_vol, size=(n, months))
    portfolio = np.full(n, float(balance))
    for m in range(months):
        portfolio = portfolio * (1 + shocks[:, m]) + float(monthly)
    portfolio = np.maximum(portfolio, 0)
    return {
        'p10':    float(np.percentile(portfolio, 10)),
        'p25':    float(np.percentile(portfolio, 25)),
        'median': float(np.median(portfolio)),
        'p75':    float(np.percentile(portfolio, 75)),
        'p90':    float(np.percentile(portfolio, 90)),
        'mean':   float(np.mean(portfolio)),
        'std':    float(np.std(portfolio)),
        'n':      n,
    }

def _display_mc_chart(checkpoints, label="Monte Carlo Projection"):
    """
    checkpoints: list of (year, mc_dict) tuples.
    Plots p10 / median / p90 lines with plotext.
    """
    years_x  = [y for y, _ in checkpoints]
    p10_y    = [mc['p10']    for _, mc in checkpoints]
    median_y = [mc['median'] for _, mc in checkpoints]
    p90_y    = [mc['p90']    for _, mc in checkpoints]
    try:
        plt.clf()
        plt.plot(years_x, p90_y,    color='green', label='90th pct')
        plt.plot(years_x, median_y, color='cyan',  label='Median')
        plt.plot(years_x, p10_y,    color='red',   label='10th pct')
        plt.title(label)
        plt.xlabel("Years")
        plt.ylabel("Value ($)")
        plt.show()
    except Exception as e:
        console.print(f"[dim]Chart unavailable: {e}[/dim]")

def _years_to_reach(target, balance, monthly, annual_return, max_years=100):
    """Binary search: how many years until FV >= target? Returns None if unreachable."""
    if balance >= target:
        return 0.0
    if _calculate_future_value(balance, monthly, annual_return, max_years) < target:
        return None
    lo, hi = 0.0, float(max_years)
    for _ in range(60):
        mid = (lo + hi) / 2
        if _calculate_future_value(balance, monthly, annual_return, mid) < target:
            lo = mid
        else:
            hi = mid
    return hi

# ── Risk Management ───────────────────────────────────────────────────────────

def _get_atr(ticker, period=14):
    """Average True Range in dollars over the last 30 days."""
    try:
        data = yf.download(ticker, period='30d', auto_adjust=True, progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.droplevel(1)
        if data.empty or len(data) < period + 1:
            return None
        high       = data['High'].squeeze().astype(float)
        low        = data['Low'].squeeze().astype(float)
        close      = data['Close'].squeeze().astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except Exception:
        return None


def _calculate_position_size(ticker, confidence, cash, risk_pct=0.02, beta=1.0):
    """ATR + beta-adjusted dynamic position sizing.

    Beta > 1.0 scales the position down proportionally — a beta-2 stock
    gets half the dollar allocation of a beta-1 stock at equal ATR.
    """
    try:
        live  = get_realtime_data(ticker)
        price = live.get('price', 0)
        if not price:
            return None
        atr = _get_atr(ticker)
        if not atr:
            atr = price * 0.02
        atr_pct     = atr / price
        dollar_size = cash * (confidence * risk_pct) / atr_pct
        # Beta adjustment: scale down for high-volatility stocks
        beta_factor = max(1.0, float(beta) if beta else 1.0)
        dollar_size /= beta_factor
        qty         = max(1, math.floor(dollar_size / price))
        return {
            'qty':         qty,
            'dollar_size': round(dollar_size, 2),
            'atr_pct':     round(atr_pct * 100, 2),
            'atr':         round(atr, 4),
            'price':       price,
            'beta':        round(beta_factor, 2),
        }
    except Exception:
        return None


def _calculate_portfolio_var(positions, confidence=0.95, lookback_days=252):
    """Historical-simulation Value at Risk for the current portfolio.

    Downloads `lookback_days` of daily returns for each position, computes
    the weighted portfolio daily P&L series, then returns the
    (1-confidence) percentile as the 1-day VaR in dollar terms.

    Returns a dict or None on failure (insufficient data / empty portfolio).
    """
    if not positions:
        return None
    try:
        tickers = [p.symbol for p in positions]
        values  = {p.symbol: float(p.market_value) for p in positions}
        total   = sum(values.values())
        if total <= 0:
            return None
        weights = {t: values[t] / total for t in tickers}

        raw = yf.download(
            tickers,
            period=f'{lookback_days}d',
            auto_adjust=True,
            progress=False,
        )
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw['Close']
        else:
            # Single ticker: flat columns (Open, High, Low, Close, Volume)
            close = raw[['Close']].rename(columns={'Close': tickers[0]})

        returns = close.pct_change().dropna()
        if returns.empty or len(returns) < 30:
            return None

        # Keep only tickers present in downloaded data
        valid = [t for t in tickers if t in returns.columns]
        port_returns = sum(weights[t] * returns[t] for t in valid)
        port_pnl     = port_returns * total

        var_dollar = float(np.percentile(port_pnl, (1 - confidence) * 100))
        cvar_dollar = float(port_pnl[port_pnl <= var_dollar].mean()) if (port_pnl <= var_dollar).any() else var_dollar

        return {
            'var_1d':        round(abs(var_dollar), 2),
            'cvar_1d':       round(abs(cvar_dollar), 2),
            'var_pct':       round(abs(var_dollar) / total * 100, 2),
            'portfolio_val': round(total, 2),
            'confidence':    confidence,
            'tickers':       valid,
            'n_days':        len(returns),
        }
    except Exception:
        return None


def _check_sell_signals(pos_list, tlh_pct=5.0):
    """Check each position for sell signals.

    Signal types:
      STOP          — price dropped ≥7% from entry (hard stop-loss)
      THESIS_BROKEN — Buffett score fell below 40
      UNDERPERFORM  — P&L in bottom 20% of portfolio (relative underperformer)
      OVERBOUGHT    — RSI > 72 (mean-reversion risk)
      TAX_LOSS      — unrealised loss ≥ tlh_pct% and no harder STOP already firing
                      (simulated tax-loss harvesting candidate; consult a tax advisor)

    Returns list of (pos, signals_list, b_score, rsi_val) 4-tuples.
    """
    n = len(pos_list)
    pnl_pcts = []
    for pos in pos_list:
        try:
            entry   = float(pos.avg_entry_price)
            current = float(pos.current_price)
            pnl_pcts.append((current - entry) / entry * 100 if entry else 0.0)
        except Exception:
            pnl_pcts.append(0.0)

    pnl_threshold = sorted(pnl_pcts)[int(len(pnl_pcts) * 0.20)] if n >= 3 else None

    results = []
    for i, pos in enumerate(pos_list):
        signals = []
        try:
            entry   = float(pos.avg_entry_price)
            current = float(pos.current_price)
        except Exception:
            entry = current = 0.0

        pnl_pct = pnl_pcts[i]

        if entry and current < entry * 0.93:
            signals.append('STOP')

        b_score = get_buffett_metrics(pos.symbol).get('score', 0)
        if b_score < 40:
            signals.append('THESIS_BROKEN')

        if pnl_threshold is not None and pnl_pct <= pnl_threshold:
            signals.append('UNDERPERFORM')

        rsi_val = None
        try:
            tech    = get_tech_indicators(pos.symbol)
            rsi_val = tech.get('rsi')
            if rsi_val and rsi_val > 72:
                signals.append('OVERBOUGHT')
        except Exception:
            pass

        # TAX_LOSS: flag a meaningful unrealised loss that hasn't already hit the
        # harder STOP threshold — surfaces as a harvest candidate for review.
        if 'STOP' not in signals and pnl_pct < -abs(tlh_pct):
            signals.append('TAX_LOSS')

        results.append((pos, signals, b_score, rsi_val))
    return results


def _show_sector_table(tickers):
    """Fetch sector for each ticker and display a sector diversity breakdown."""
    sector_counts: Counter = Counter()
    for t in tickers:
        try:
            sector = yf.Ticker(t).info.get('sector', 'Unknown')
        except Exception:
            sector = 'Unknown'
        sector_counts[sector] += 1
    total = sum(sector_counts.values()) or 1
    tbl = Table(title="Sector Breakdown", box=box.SIMPLE, header_style="bold dim")
    tbl.add_column("Sector", style="bold")
    tbl.add_column("Count", justify="right")
    tbl.add_column("Weight", justify="right")
    for sector, count in sector_counts.most_common():
        pct   = count / total
        color = "green" if pct < 0.30 else "yellow" if pct < 0.50 else "red"
        tbl.add_row(sector, str(count), f"[{color}]{pct:.0%}[/{color}]")
    console.print(tbl)


# ── Backtesting ────────────────────────────────────────────────────────────────

def _compute_rsi(series, period=14):
    """Compute RSI as a full pandas Series."""
    delta = series.diff()
    gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))


def get_multiframe_signals(ticker: str) -> dict:
    """Daily / weekly / monthly technical signals from 1-year price history.

    Returns:
        rsi_1d   — 14-day RSI on daily close
        rsi_1w   — 14-week RSI on weekly close (None if insufficient data)
        trend_1mo — 'UP'|'DOWN' based on SMA-3 vs SMA-12 on monthly close
        above_sma50 — bool, whether today's price is above the 50-day SMA
    """
    try:
        df = yf.download(ticker, period='1y', progress=False, auto_adjust=True)
        if df.empty or len(df) < 20:
            return {}
        close = df['Close']
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()

        # Daily RSI
        rsi_1d = round(float(_compute_rsi(close).iloc[-1]), 1)

        # Weekly RSI — resample to weekly, need ≥15 weeks
        close_w = close.resample('W').last().dropna()
        rsi_1w: float | None = None
        if len(close_w) >= 15:
            rsi_1w = round(float(_compute_rsi(close_w).iloc[-1]), 1)

        # Monthly trend: 3-month SMA vs 12-month SMA on monthly close
        close_m = close.resample('ME').last().dropna()
        trend_1mo: str | None = None
        if len(close_m) >= 3:
            n_long    = min(12, len(close_m))
            sma3_m    = float(close_m.rolling(3).mean().iloc[-1])
            sma_long  = float(close_m.rolling(n_long).mean().iloc[-1])
            trend_1mo = 'UP' if sma3_m > sma_long else 'DOWN'

        # 50-day SMA vs current price
        sma50_val   = close.rolling(50).mean().iloc[-1]
        above_sma50 = bool(float(close.iloc[-1]) > float(sma50_val)) \
                      if not np.isnan(sma50_val) else None

        return {
            'rsi_1d':      rsi_1d,
            'rsi_1w':      rsi_1w,
            'trend_1mo':   trend_1mo,
            'above_sma50': above_sma50,
        }
    except Exception:
        return {}


def _calculate_sharpe(daily_returns, risk_free_annual=0.05):
    """Annualised Sharpe ratio."""
    excess = daily_returns - risk_free_annual / 252
    std    = excess.std()
    if std == 0:
        return 0.0
    return float(excess.mean() / std * (252 ** 0.5))


def _calculate_max_drawdown(equity_curve):
    """Maximum peak-to-trough drawdown as a positive fraction."""
    eq     = np.array(equity_curve, dtype=float)
    peaks  = np.maximum.accumulate(eq)
    denom  = np.where(peaks == 0, 1.0, peaks)
    return float(((peaks - eq) / denom).max())


def _run_backtest(ticker, period_years, initial_capital, compare_spy, buy_and_hold=False):
    """RSI-based backtest. Returns (metrics, equity_curve, dates, spy_metrics_or_None).

    Signal: BUY when rsi<35 and close>sma50; SELL when rsi>70 or close<entry*0.93.
    buy_and_hold=True: buy all shares day-0 and hold (used for SPY benchmark).
    """
    try:
        period_str = f"{int(period_years * 365)}d"
        data = yf.download(ticker, period=period_str, auto_adjust=True, progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.droplevel(1)
        if len(data) < 60:
            return None, [], [], None

        close = data['Close'].squeeze().astype(float)
        sma50 = close.rolling(50).mean()
        rsi   = _compute_rsi(close)

        cash    = float(initial_capital)
        shares  = 0.0
        entry_price = 0.0
        equity_curve = []
        dates        = []
        trades       = []

        if buy_and_hold:
            buy_price = float(close.iloc[0])
            shares    = cash / buy_price
            cash      = 0.0
            for i in range(len(close)):
                equity_curve.append(cash + shares * float(close.iloc[i]))
                dates.append(str(data.index[i])[:10])
            total_return = (equity_curve[-1] - initial_capital) / initial_capital
            cagr = (equity_curve[-1] / initial_capital) ** (1 / max(period_years, 0.01)) - 1
            eq   = np.array(equity_curve, dtype=float)
            dr   = pd.Series(np.diff(eq) / np.where(eq[:-1] == 0, 1.0, eq[:-1]))
            return {
                'ticker': ticker, 'total_return': total_return, 'cagr': cagr,
                'sharpe': _calculate_sharpe(dr),
                'max_drawdown': _calculate_max_drawdown(equity_curve),
                'win_rate': None, 'avg_win': None, 'avg_loss': None,
                'profit_factor': None, 'n_trades': None, 'buy_and_hold': True,
            }, equity_curve, dates, None

        for i in range(len(close)):
            price = float(close.iloc[i])
            r     = float(rsi.iloc[i])   if not pd.isna(rsi.iloc[i])   else 50.0
            sma   = float(sma50.iloc[i]) if not pd.isna(sma50.iloc[i]) else price

            if shares == 0:
                if r < 35 and price > sma:
                    shares      = cash / price
                    cash        = 0.0
                    entry_price = price
            else:
                if r > 70 or price < entry_price * 0.93:
                    trades.append((entry_price, price))
                    cash        = shares * price
                    shares      = 0.0
                    entry_price = 0.0

            equity_curve.append(cash + shares * price)
            dates.append(str(data.index[i])[:10])

        if shares > 0:
            last_price = float(close.iloc[-1])
            trades.append((entry_price, last_price))
            cash           = shares * last_price
            equity_curve[-1] = cash

        total_return = (equity_curve[-1] - initial_capital) / initial_capital
        cagr         = (equity_curve[-1] / initial_capital) ** (1 / max(period_years, 0.01)) - 1
        eq           = np.array(equity_curve, dtype=float)
        dr           = pd.Series(np.diff(eq) / np.where(eq[:-1] == 0, 1.0, eq[:-1]))

        wins   = [t[1] - t[0] for t in trades if t[1] >= t[0]]
        losses = [t[0] - t[1] for t in trades if t[1] <  t[0]]
        win_rate      = len(wins) / len(trades) if trades else 0.0
        avg_win       = float(np.mean(wins))   if wins   else 0.0
        avg_loss      = float(np.mean(losses)) if losses else 0.0
        profit_factor = sum(wins) / sum(losses) if sum(losses) > 0 else float('inf')

        metrics = {
            'ticker': ticker, 'total_return': total_return, 'cagr': cagr,
            'sharpe': _calculate_sharpe(dr),
            'max_drawdown': _calculate_max_drawdown(equity_curve),
            'win_rate': win_rate, 'avg_win': avg_win, 'avg_loss': avg_loss,
            'profit_factor': profit_factor, 'n_trades': len(trades), 'buy_and_hold': False,
        }

        spy_metrics = None
        if compare_spy:
            spy_metrics, _, _, _ = _run_backtest(
                'SPY', period_years, initial_capital, compare_spy=False, buy_and_hold=True)

        return metrics, equity_curve, dates, spy_metrics

    except Exception as e:
        console.print(f"[red]Backtest error: {e}[/red]")
        return None, [], [], None

# ── Commands ──────────────────────────────────────────────────────────────────

@cli.command()
@click.argument('question')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def ask(question, primary_model):
    """Ask the AI a free-form investing question: buffet-bot ask "What is a P/E ratio?" """
    ticker_context = ""
    try:
        results = yf.Search(question).quotes
        if results:
            symbols = [q.get('symbol', '') for q in results[:5] if q.get('symbol')]
            if symbols:
                ticker_context = f"\nRelated tickers: {', '.join(symbols)}"
    except Exception:
        pass

    prompt = f"""You are a knowledgeable investing assistant guided by Warren Buffett's value investing principles.
Answer the following question thoughtfully and concisely, referencing relevant financial concepts where appropriate.{ticker_context}

Question: {question}"""

    console.print(Panel(question, title="[bold]Question[/bold]", border_style="blue"))
    responses = _query_llms_freeform(prompt, primary_model)
    for model_name, response_text in responses.items():
        color = MODEL_COLORS.get(model_name, 'white')
        console.print(Panel(response_text, title=f"[bold {color}]{model_name}[/bold {color}]",
                            border_style=color))

@cli.command()
@click.argument('query')
def lookup(query):
    """Look up a ticker by company name: buffet-bot lookup Apple"""
    try:
        results = yf.Search(query).quotes
    except Exception as e:
        console.print(f"[red]Search error: {e}[/red]")
        return

    if not results:
        console.print(f"[yellow]No results found for '{query}'.[/yellow]")
        return

    table = Table(title=f"Search results for: {query}", box=box.ROUNDED, header_style="bold blue")
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Company Name")
    table.add_column("Exchange", style="dim")
    table.add_column("Type", style="dim")

    for q in results:
        table.add_row(
            q.get('symbol', ''),
            q.get('longname') or q.get('shortname', ''),
            q.get('exchange', ''),
            q.get('quoteType', ''),
        )

    console.print(table)
    console.print("\n[dim]Tip: Run 'buffet-bot analyze <SYMBOL>' to analyze any ticker above.[/dim]")

@cli.command()
@click.argument('query', required=False, default=None,
                metavar='[QUERY]')
@click.option('--sector', default=None,
              type=click.Choice(SECTORS, case_sensitive=False),
              help='Filter by sector (e.g. Technology, Healthcare).')
@click.option('--limit', default=50, show_default=True, type=int,
              help='Max companies to display.')
@click.option('--all', 'full_universe', is_flag=True, default=False,
              help='Search the full SEC EDGAR universe (10,000+ companies, requires network).')
def browse(query, sector, limit, full_universe):
    """Browse or search the investable universe: buffet-bot browse --sector Technology

    \b
    Examples:
      buffet-bot browse                        # sector overview
      buffet-bot browse --sector Healthcare    # list healthcare companies
      buffet-bot browse "electric vehicle"     # search by keyword (bundled DB)
      buffet-bot browse "bank" --all           # search full EDGAR universe (10K+)
    """
    _TIP = "\n[dim]Tip: run [bold]buffet-bot analyze TICKER[/bold] or [bold]buffet-bot lookup TICKER[/bold] for detail.[/dim]"

    # ── Full EDGAR universe search ─────────────────────────────────────────
    if full_universe:
        if not query:
            console.print("[yellow]Provide a search term with --all, e.g.: buffet-bot browse apple --all[/yellow]")
            return
        console.print(Panel(
            f"Searching SEC EDGAR for [bold]{query}[/bold] across [bold cyan]10,000+[/bold cyan] companies...",
            title="[bold]SEC EDGAR Universe Search[/bold]",
            border_style="blue",
        ))
        with console.status("[dim]Fetching EDGAR company list...[/dim]", spinner="dots"):
            try:
                results = search_edgar(query, limit=limit)
            except Exception as e:
                console.print(f"[red]EDGAR search failed: {e}[/red]")
                return
        if not results:
            console.print(f"[yellow]No EDGAR results for '{query}'.[/yellow]")
            return
        tbl = Table(
            title=f"EDGAR Results for '{query}' ({len(results)} shown)",
            box=box.ROUNDED, header_style="bold blue",
        )
        tbl.add_column("Ticker",  style="bold cyan", min_width=8,  no_wrap=True)
        tbl.add_column("Company Name",               min_width=35)
        tbl.add_column("CIK",     style="dim",       min_width=10, no_wrap=True)
        for r in results:
            tbl.add_row(r["ticker"], r["name"].title(), r["cik"])
        console.print(tbl)
        console.print(f"[dim]Source: SEC EDGAR company_tickers.json — {len(results)} match(es)[/dim]")
        console.print(_TIP)
        return

    # ── Keyword search in bundled DB ───────────────────────────────────────
    if query and not sector:
        results = search_companies(query, limit=limit)
        if not results:
            # yfinance fallback
            console.print(f"[dim]No bundled results for '{query}' — trying yfinance search...[/dim]")
            try:
                yf_results = yf.Search(query).quotes
                if not yf_results:
                    console.print(f"[yellow]No results found for '{query}'. Try --all for the full EDGAR universe.[/yellow]")
                    return
                tbl = Table(
                    title=f"Search results: '{query}'",
                    box=box.ROUNDED, header_style="bold blue",
                )
                tbl.add_column("Ticker",       style="bold cyan", min_width=8)
                tbl.add_column("Company Name",                    min_width=30)
                tbl.add_column("Exchange",     style="dim",       min_width=10)
                tbl.add_column("Type",         style="dim",       min_width=10)
                for q in yf_results[:limit]:
                    tbl.add_row(
                        q.get("symbol", ""),
                        q.get("longname") or q.get("shortname", ""),
                        q.get("exchange", ""),
                        q.get("quoteType", ""),
                    )
                console.print(tbl)
                console.print(_TIP)
                return
            except Exception as e:
                console.print(f"[yellow]Search unavailable: {e}. Try --all for the full EDGAR universe.[/yellow]")
                return
        tbl = Table(
            title=f"Search results: '{query}'  ({len(results)} match(es))",
            box=box.ROUNDED, header_style="bold blue",
        )
        tbl.add_column("Ticker",   style="bold cyan", min_width=8,  no_wrap=True)
        tbl.add_column("Company",                     min_width=35)
        tbl.add_column("Sector",   style="dim",       min_width=22)
        tbl.add_column("Exchange", style="dim",       min_width=8)
        for r in results:
            tbl.add_row(r["ticker"], r["name"], r["sector"], r["exchange"])
        console.print(tbl)
        console.print(_TIP)
        return

    # ── Sector listing ─────────────────────────────────────────────────────
    if sector:
        companies = list_companies(sector=sector, limit=limit)
        tbl = Table(
            title=f"[bold]{sector}[/bold] — {len(companies)} companies",
            box=box.ROUNDED, header_style="bold blue",
        )
        tbl.add_column("#",        style="dim",       justify="right", min_width=4)
        tbl.add_column("Ticker",   style="bold cyan", min_width=8,  no_wrap=True)
        tbl.add_column("Company",                     min_width=38)
        tbl.add_column("Exchange", style="dim",       min_width=8)
        for i, c in enumerate(companies, 1):
            tbl.add_row(str(i), c["ticker"], c["name"], c["exchange"])
        console.print(tbl)
        console.print(_TIP)
        return

    # ── Sector overview (no args) ──────────────────────────────────────────
    from buffet_bot.universe import _COMPANY_DB as _DB
    sector_counts: dict[str, int] = {}
    for v in _DB.values():
        sector_counts[v["sector"]] = sector_counts.get(v["sector"], 0) + 1

    tbl = Table(
        title=f"[bold]Investable Universe[/bold]  —  {len(_DB)} companies across {len(SECTORS)} sectors",
        box=box.ROUNDED, header_style="bold blue",
    )
    tbl.add_column("Sector",          min_width=28)
    tbl.add_column("Companies",       justify="right", min_width=11)
    tbl.add_column("Sample Tickers",  style="dim",     min_width=35)

    for s in SECTORS:
        count   = sector_counts.get(s, 0)
        samples = [t for t, v in _DB.items() if v["sector"] == s][:5]
        tbl.add_row(f"[bold]{s}[/bold]", str(count), ", ".join(samples))

    console.print(tbl)
    console.print(
        "\n[dim]Filter by sector:  [bold]buffet-bot browse --sector Technology[/bold]\n"
        "Search by keyword:  [bold]buffet-bot browse \"electric vehicle\"[/bold]\n"
        "Full universe:      [bold]buffet-bot browse \"bank\" --all[/bold]  "
        f"(10,000+ companies via SEC EDGAR)[/dim]"
    )

@cli.command()
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--risk', type=click.Choice(['low', 'medium', 'high']), default=None,
              help='Risk tolerance [default: from config or medium].')
@click.option('--dry-run/--execute', default=True)
@click.option('--model', 'primary_model', default=None, type=click.Choice(MODELS),
              help='Primary Ollama model [default: from config or deepseek-r1].')
@click.option('--strategy', type=click.Choice(['value', 'growth', 'dividend', 'turnaround']),
              default=None, help='Investment strategy lens [default: from config or value].')
@click.option('--json', 'as_json', is_flag=True, default=False,
              help='Output result as JSON (suppresses Rich output).')
def analyze(ticker, risk, dry_run, primary_model, strategy, as_json):
    """Analyze stock or crypto: buffet-bot analyze AAPL / buffet-bot analyze BTC/USD"""
    ticker = ticker.upper()
    cfg = _CONFIG['defaults']
    if primary_model is None: primary_model = cfg.get('model', MODELS[0])
    if risk is None:          risk          = cfg.get('risk', 'medium')
    if strategy is None:      strategy      = cfg.get('strategy', 'value')

    # ── Crypto routing ────────────────────────────────────────────────────────
    if is_crypto_symbol(ticker):
        _analyze_crypto(ticker, dry_run, primary_model, console, MODELS, MODEL_COLORS)
        return

    if not as_json:
        console.print(Panel(
            f"[bold]{ticker}[/bold]  |  Risk: [yellow]{risk}[/yellow]  |  Strategy: [cyan]{strategy}[/cyan]",
            title="Analyzing", border_style="blue"))

    result = _run_analysis(ticker, risk, primary_model, strategy)

    if as_json:
        best = result.get('best_buy_resp') or {}
        output = {
            'ticker':        ticker,
            'timestamp':     datetime.now(timezone.utc).isoformat(),
            'consensus':     result['consensus'],
            'confidence':    best.get('confidence'),
            'qty':           best.get('qty'),
            'stop_pct':      best.get('stop_pct'),
            'reason':        best.get('reason'),
            'buffett_score': result['buffett'].get('score'),
            'price':         result['realtime'].get('price'),
            'change_pct':    result['realtime'].get('change_pct'),
            'sentiment':     result['sentiment'].get('overall'),
            'models':        {m: r.get('action') for m, r in result['responses'].items()
                              if isinstance(r, dict)},
        }
        click.echo(json.dumps(output, indent=2))
        return

    _print_live_market(ticker, result['realtime'], result['news'])
    earnings = _get_earnings_date(ticker)
    if earnings:
        days_away = (datetime.strptime(earnings['date'], '%Y-%m-%d') - datetime.utcnow()).days + 1
        timing = earnings['time'].replace('time-', '').replace('-', ' ')
        console.print(Panel(
            f"[bold yellow]Earnings in {days_away} day(s)[/bold yellow] "
            f"({timing}) — {earnings['fiscal_quarter']}  EPS est: {earnings['eps_forecast']}",
            title="[bold yellow]Upcoming Earnings Warning[/bold yellow]",
            border_style="yellow",
        ))
    analyst = result.get('analyst', {})
    if analyst:
        upside = analyst.get('upside_pct')
        sign   = '+' if (upside or 0) >= 0 else ''
        u_color = 'green' if (upside or 0) >= 5 else ('red' if (upside or 0) < 0 else 'yellow')
        rating  = analyst.get('rating_key', 'N/A')
        r_color = ('green' if rating in ('BUY', 'STRONG BUY') else
                   'red'   if rating in ('SELL', 'UNDERPERFORM') else 'yellow')
        changes_str = ''
        for ch in analyst.get('recent_changes', [])[:3]:
            changes_str += f"\n  {ch['firm']}: {ch['from'] or '?'} → {ch['to']} ({ch['action']})"
        console.print(Panel(
            f"Consensus:  [{r_color}][bold]{rating}[/bold][/{r_color}]"
            f"  [dim](mean score {analyst.get('rating_mean', '?')}/5.0, "
            f"{analyst.get('num_analysts', '?')} analysts)[/dim]\n"
            f"Price target: [bold]${analyst.get('target_mean', 'N/A')}[/bold]  "
            f"[dim](low ${analyst.get('target_low', '?')} — high ${analyst.get('target_high', '?')})[/dim]\n"
            f"Implied upside: [{u_color}][bold]{sign}{upside}%[/bold][/{u_color}]"
            + (f"\nRecent changes:{changes_str}" if changes_str else ''),
            title="[bold]Wall Street Analyst Consensus[/bold]",
            border_style=r_color,
        ))

    _print_ai_responses(result['responses'])
    console.print(f"\nConsensus: {_consensus_text(result['consensus'])}")
    data_src = result['realtime'].get('source', 'yfinance')
    console.print(
        f"[dim]Analysis at {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"Price via {data_src} | Risk: {risk} | Strategy: {strategy}[/dim]"
    )

    sizing = None
    if result['consensus'] == 'BUY':
        try:
            account    = trading_client.get_account()
            cash       = float(account.cash)
            confidence = result['best_buy_resp'].get('confidence', 0.5) if result['best_buy_resp'] else 0.5
            beta       = result['buffett'].get('beta', 1.0)
            sizing     = _calculate_position_size(ticker, confidence, cash, beta=beta)
            if sizing:
                llm_qty    = result['best_buy_resp'].get('qty', 1) if result['best_buy_resp'] else 1
                beta_note  = f"  |  Beta: [yellow]{sizing['beta']}x[/yellow]" if sizing['beta'] != 1.0 else ""
                console.print(Panel(
                    f"Account cash: [bold]${cash:,.2f}[/bold]\n"
                    f"LLM suggested qty:   [dim]{llm_qty}[/dim]\n"
                    f"Formula qty:         [bold cyan]{sizing['qty']}[/bold cyan]  "
                    f"(${sizing['dollar_size']:,.2f})\n"
                    f"ATR: ${sizing['atr']:.2f}  ({sizing['atr_pct']}% of price){beta_note}",
                    title="[bold cyan]Dynamic Position Sizing[/bold cyan]",
                    border_style="cyan",
                ))
        except Exception:
            sizing = None

    if not dry_run and result['consensus'] == 'BUY':
        if click.confirm(f'Execute BUY {ticker}? (Paper)'):
            if result['best_buy_resp']:
                best = dict(result['best_buy_resp'])
                if sizing:
                    best['qty'] = sizing['qty']
                _place_order(ticker, best)
            else:
                console.print("[yellow]No valid BUY signal[/yellow]")

@cli.command()
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--risk', type=click.Choice(['low', 'medium', 'high']), default=None,
              help='Risk tolerance [default: from config or medium].')
@click.option('--model', 'primary_model', default=None, type=click.Choice(MODELS),
              help='Primary Ollama model [default: from config or deepseek-r1].')
@click.option('--strategy', type=click.Choice(['value', 'growth', 'dividend', 'turnaround']),
              default=None, help='Investment strategy lens [default: from config or value].')
def buy(ticker, risk, primary_model, strategy):
    """Analyze then immediately prompt to buy: buffet-bot buy AAPL --strategy dividend"""
    cfg = _CONFIG['defaults']
    if primary_model is None: primary_model = cfg.get('model', MODELS[0])
    if risk is None:          risk          = cfg.get('risk', 'medium')
    if strategy is None:      strategy      = cfg.get('strategy', 'value')
    console.print(Panel(
        f"[bold]{ticker}[/bold]  |  Risk: [yellow]{risk}[/yellow]  |  Strategy: [cyan]{strategy}[/cyan]",
        title="Analyzing", border_style="blue"))

    result = _run_analysis(ticker, risk, primary_model, strategy)
    _print_live_market(ticker, result['realtime'], result['news'])
    _print_ai_responses(result['responses'])
    console.print(f"\nConsensus: {_consensus_text(result['consensus'])}")

    if result['consensus'] != 'BUY':
        console.print(f"[yellow]Consensus is {result['consensus']} — no order placed.[/yellow]")
        return

    if not result['best_buy_resp']:
        console.print("[yellow]No valid BUY signal from models — no order placed.[/yellow]")
        return

    sizing = None
    try:
        account    = trading_client.get_account()
        cash       = float(account.cash)
        confidence = result['best_buy_resp'].get('confidence', 0.5)
        beta       = result['buffett'].get('beta', 1.0)
        sizing     = _calculate_position_size(ticker, confidence, cash, beta=beta)
        if sizing:
            llm_qty   = result['best_buy_resp'].get('qty', 1)
            beta_note = f"  |  Beta: [yellow]{sizing['beta']}x[/yellow]" if sizing['beta'] != 1.0 else ""
            console.print(Panel(
                f"Account cash: [bold]${cash:,.2f}[/bold]\n"
                f"LLM suggested qty:   [dim]{llm_qty}[/dim]\n"
                f"Formula qty:         [bold cyan]{sizing['qty']}[/bold cyan]  "
                f"(${sizing['dollar_size']:,.2f})\n"
                f"ATR: ${sizing['atr']:.2f}  ({sizing['atr_pct']}% of price){beta_note}",
                title="[bold cyan]Dynamic Position Sizing[/bold cyan]",
                border_style="cyan",
            ))
    except Exception:
        sizing = None

    if click.confirm(f'Execute BUY {ticker}? (Paper)'):
        best = dict(result['best_buy_resp'])
        if sizing:
            best['qty'] = sizing['qty']
        _place_order(ticker, best)

@cli.command()
@click.option('--limit', default=20, show_default=True, help='Max number of orders to show.')
@click.option('--ticker', default=None, help='Filter by ticker symbol.')
@click.option('--status', 'order_status', default='all',
              type=click.Choice(['all', 'open', 'closed']), show_default=True)
def history(limit, ticker, order_status):
    """Show past paper trades: buffet-bot history --ticker AAPL"""
    status_map = {
        'all': QueryOrderStatus.ALL,
        'open': QueryOrderStatus.OPEN,
        'closed': QueryOrderStatus.CLOSED,
    }
    request = GetOrdersRequest(
        status=status_map[order_status],
        limit=limit,
        symbols=[ticker.upper()] if ticker else None,
    )
    try:
        orders = trading_client.get_orders(filter=request)
    except Exception as e:
        console.print(f"[red]Error fetching orders: {e}[/red]")
        return

    if not orders:
        console.print("[yellow]No orders found.[/yellow]")
        return

    table = Table(title="Trade History", box=box.ROUNDED, header_style="bold blue")
    table.add_column("Date", style="dim")
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Side")
    table.add_column("Qty")
    table.add_column("Fill Price")
    table.add_column("Status")

    for o in orders:
        side_color = "green" if o.side == OrderSide.BUY else "red"
        table.add_row(
            str(o.submitted_at)[:19] if o.submitted_at else '',
            o.symbol,
            f"[{side_color}]{o.side.value.upper()}[/{side_color}]",
            str(o.qty),
            str(o.filled_avg_price or '—'),
            o.status.value,
        )

    console.print(table)

@cli.command()
@click.option('--period', default='1M',
              type=click.Choice(['1D', '1W', '1M', '3M', '6M', '1A']),
              show_default=True, help='History period.')
def portfolio(period):
    """Show a terminal line chart of portfolio equity over time."""
    url = f"{ALPACA_PAPER_BASE}/v2/account/portfolio/history"
    headers = {
        'APCA-API-KEY-ID': API_KEY,
        'APCA-API-SECRET-KEY': SECRET_KEY,
    }
    timeframe = '1D' if period != '1D' else '1H'
    params = {'period': period, 'timeframe': timeframe}

    try:
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        console.print(f"[red]Error fetching portfolio history: {e}[/red]")
        return

    timestamps = data.get('timestamp', [])
    equity = data.get('equity', [])

    if not timestamps or not equity:
        console.print("[yellow]No portfolio history available yet.[/yellow]")
        return

    # Convert unix timestamps to date strings
    dates = [datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d') for ts in timestamps]

    # Strip None values
    pairs = [(d, e) for d, e in zip(dates, equity) if e is not None]
    if not pairs:
        console.print("[yellow]No equity data points to display.[/yellow]")
        return
    dates, equity = zip(*pairs)

    pnl = float(equity[-1]) - float(equity[0])
    pnl_color = "green" if pnl >= 0 else "red"
    pnl_sign = "+" if pnl >= 0 else ""

    try:
        plt.clf()
        plt.plot(list(dates), [float(e) for e in equity], color='cyan', marker='dot')
        plt.title(f"Portfolio Equity — {period}")
        plt.xlabel("Date")
        plt.ylabel("USD")
        plt.show()
    except Exception as e:
        console.print(f"[red]Chart error: {e}[/red]")

    console.print(
        f"\nStart: [bold]${float(equity[0]):,.2f}[/bold]  "
        f"Current: [bold]${float(equity[-1]):,.2f}[/bold]  "
        f"P&L: [{pnl_color}][bold]{pnl_sign}${pnl:,.2f}[/bold][/{pnl_color}]"
    )

@cli.command()
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def chat(primary_model):
    """Interactive multi-turn investing discussion with both AI models."""
    models_in_session = [primary_model]
    if primary_model != MODELS[1]:
        models_in_session.append(MODELS[1])

    # Separate conversation history per model
    histories = {m: [
        {'role': 'system', 'content':
         "You are an expert investing assistant guided by Warren Buffett's value investing principles. "
         "Be concise, insightful, and reference real financial data when possible."}
    ] for m in models_in_session}

    console.print(Panel(
        "[bold]Buffett AI Planning Session[/bold]\n\n"
        f"Models: {', '.join(f'[bold]{m}[/bold]' for m in models_in_session)}\n\n"
        "[dim]Type your question or topic. Both models will respond.\n"
        "Commands:  [bold]exit[/bold] or [bold]quit[/bold] to end  |  "
        "[bold]clear[/bold] to reset conversation history[/dim]",
        border_style="blue",
    ))

    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Session ended.[/dim]")
            break

        if not user_input.strip():
            continue

        if user_input.strip().lower() in ('exit', 'quit', 'q'):
            console.print("[dim]Session ended.[/dim]")
            break

        if user_input.strip().lower() == 'clear':
            for m in models_in_session:
                histories[m] = [histories[m][0]]  # keep system prompt
            console.print("[dim]Conversation history cleared.[/dim]")
            continue

        # Append user turn to all histories
        for m in models_in_session:
            histories[m].append({'role': 'user', 'content': user_input})

        # Query each model and display response
        for model in models_in_session:
            color = MODEL_COLORS.get(model, 'white')
            try:
                resp = ollama.chat(
                    model=model,
                    messages=histories[model],
                    options={'temperature': 0.5},
                )
                reply = resp['message']['content'].strip()
                histories[model].append({'role': 'assistant', 'content': reply})
                console.print(Panel(reply,
                                    title=f"[bold {color}]{model}[/bold {color}]",
                                    border_style=color))
            except Exception as e:
                console.print(f"[red]{model} error: {e}[/red]")
                histories[model].pop()  # remove unanswered user turn for this model

@cli.command()
@click.option('--watchlist', 'use_watchlist', is_flag=True, default=False,
              help='Scan your saved watchlist instead of the default tickers.')
@click.option('--top', default=5, show_default=True, type=int,
              help='Number of top results to show (0 = show all).')
@click.option('--json', 'as_json', is_flag=True, default=False,
              help='Output results as JSON (suppresses Rich output).')
@click.option('--notify', 'as_notify', is_flag=True, default=False,
              help='Plain-text report for cron/email — pipe to sendmail or a script.')
@click.option('--min-score', 'min_score', default=60, show_default=True, type=int,
              help='Minimum Buffett score to include in --notify BUY CANDIDATES list.')
def scan(use_watchlist, top, as_json, as_notify, min_score):
    """Scan top stocks for Buffett opportunities"""
    default_tickers = ['AAPL', 'MSFT', 'GOOGL', 'BRK-B', 'JNJ', 'V', 'JPM', 'PG']
    if use_watchlist:
        saved = get_watchlist()
        tickers = [w['ticker'] for w in saved] if saved else default_tickers
        if not saved and not as_json and not as_notify:
            console.print("[dim yellow]Watchlist is empty — using default tickers.[/dim yellow]")
    else:
        tickers = default_tickers

    all_metrics = {}
    scan_ctx = (console.status("[bold blue]Scanning tickers (concurrent)...[/bold blue]")
                if not as_json and not as_notify else contextlib.nullcontext())
    with scan_ctx:
        with ThreadPoolExecutor(max_workers=len(tickers)) as pool:
            futures = {pool.submit(get_buffett_metrics, t): t for t in tickers}
            for fut in as_completed(futures):
                t = futures[fut]
                try:
                    all_metrics[t] = fut.result()
                except Exception:
                    all_metrics[t] = {'score': 0}

    ranked = sorted(all_metrics.items(), key=lambda x: x[1].get('score', 0), reverse=True)
    if top > 0:
        ranked = ranked[:top]

    if as_json:
        click.echo(json.dumps(
            [{'ticker': t, 'buffett_score': m.get('score', 0)} for t, m in ranked],
            indent=2,
        ))
        return

    if as_notify:
        def _f(v, w):
            return f"{v:.1f}".rjust(w) if v is not None else '-'.rjust(w)

        scanned_at = datetime.now().strftime('%Y-%m-%d %H:%M')
        sep = '=' * 50
        click.echo("BUFFET-BOT SCAN REPORT")
        click.echo(f"Date:    {scanned_at}")
        click.echo(f"Tickers: {' '.join(tickers)}")
        click.echo(sep)
        click.echo(f"{'RNK':<4} {'TICKER':<8} {'SCORE':>5}  {'ROE%':>6}  {'DEBT/EQ':>7}  {'OPMGN%':>7}  {'P/E':>6}")
        click.echo('-' * 50)
        for rank, (ticker, m) in enumerate(ranked, 1):
            score  = m.get('score', 0)
            roe    = m.get('roe', None)
            debt   = m.get('debt_eq', None)
            margin = m.get('op_margin', None)
            pe     = m.get('pe', None)
            click.echo(
                f"{rank:<4} {ticker:<8} {score:>5}  "
                f"{_f(roe, 6)}  {_f(debt, 7)}  {_f(margin, 7)}  {_f(pe, 6)}"
            )
        click.echo(sep)
        candidates = [t for t, m in ranked if m.get('score', 0) >= min_score]
        if candidates:
            scores_str = ', '.join(
                f"{t}({all_metrics[t].get('score', 0)})" for t in candidates
            )
            click.echo(f"BUY CANDIDATES (score >= {min_score}): {scores_str}")
        else:
            click.echo(f"BUY CANDIDATES (score >= {min_score}): none")
        click.echo(f"Scanned {len(tickers)} tickers at {scanned_at}.")
        click.echo("Run: buffet-bot analyze <TICKER> for full LLM analysis.")
        return

    scanned_at = datetime.now().strftime('%Y-%m-%d %H:%M')
    table = Table(
        title=f"Buffett Scan — {scanned_at}",
        box=box.ROUNDED, header_style="bold blue",
    )
    table.add_column("Rank",  justify="right", style="dim")
    table.add_column("Ticker", style="bold cyan", no_wrap=True)
    table.add_column("Score",  justify="right")
    table.add_column("ROE%",   justify="right")
    table.add_column("Debt/Eq", justify="right")
    table.add_column("OpMgn%", justify="right")
    table.add_column("P/E",    justify="right")

    for rank, (ticker, m) in enumerate(ranked, 1):
        score  = m.get('score', 0)
        color  = _score_color(score)
        roe    = m.get('roe',      '—')
        debt   = m.get('debt_eq', '—')
        margin = m.get('op_margin','—')
        pe     = m.get('pe',       '—')
        table.add_row(
            str(rank),
            ticker,
            f"[{color}]{score}[/{color}]",
            f"{roe}" if isinstance(roe, str) else f"{roe:.1f}",
            f"{debt}" if isinstance(debt, str) else f"{debt:.1f}",
            f"{margin}" if isinstance(margin, str) else f"{margin:.1f}",
            f"{pe}" if isinstance(pe, str) else f"{pe:.1f}",
        )

    console.print(table)
    console.print(f"[dim]Scanned {len(tickers)} tickers at {scanned_at}[/dim]")

@cli.command()
def status():
    """Check account status — Alpaca paper, Coinbase, and IBKR if configured."""
    # ── Alpaca paper ─────────────────────────────────────────────────────────
    account = trading_client.get_account()
    console.print(Panel(
        f"Cash:          [bold green]${float(account.cash):,.2f}[/bold green]\n"
        f"Buying Power:  [bold cyan]${float(account.buying_power):,.2f}[/bold cyan]",
        title="[bold]Alpaca Paper Account[/bold]",
        border_style="blue",
    ))

    # ── Coinbase (optional) ───────────────────────────────────────────────────
    cb_key = os.getenv("COINBASE_API_KEY")
    if cb_key:
        with console.status("[dim]Fetching Coinbase balance...[/dim]"):
            cb = get_coinbase_balance()
        if cb:
            rows = "\n".join(
                f"  {r['currency']}: [bold]{r['balance']:,.6f}[/bold]"
                for r in cb["accounts"]
            )
            console.print(Panel(
                f"[bold green]USD Cash: ${cb['total_usd']:,.2f}[/bold green]\n{rows}",
                title="[bold]Coinbase (Live)[/bold]",
                border_style="yellow",
            ))
        else:
            console.print("[dim]Coinbase: connected but could not fetch balances.[/dim]")
    else:
        console.print("[dim]Coinbase: not configured (set COINBASE_API_KEY to enable).[/dim]")

    # ── IBKR (optional) ───────────────────────────────────────────────────────
    ibkr_acct = os.getenv("IBKR_ACCOUNT_ID")
    if ibkr_acct:
        with console.status("[dim]Connecting to IBKR (TWS/IB Gateway)...[/dim]"):
            ibkr = get_ibkr_status()
        if ibkr:
            console.print(Panel(
                f"Net Liquidation: [bold green]${ibkr.get('NetLiquidation', 0):,.2f}[/bold green]\n"
                f"Total Cash:      [bold cyan]${ibkr.get('TotalCashValue', 0):,.2f}[/bold cyan]\n"
                f"Buying Power:    [bold cyan]${ibkr.get('BuyingPower', 0):,.2f}[/bold cyan]",
                title=f"[bold]IBKR — {ibkr.get('account', ibkr_acct)}[/bold]",
                border_style="magenta",
            ))
        else:
            console.print(
                "[dim]IBKR: could not connect — is TWS or IB Gateway running on "
                f"{os.getenv('IBKR_HOST', '127.0.0.1')}:{os.getenv('IBKR_PORT', '7497')}?[/dim]"
            )
    else:
        console.print("[dim]IBKR: not configured (set IBKR_ACCOUNT_ID to enable).[/dim]")

@cli.command()
@click.option('--plan', default=None, help='Load and run a saved plan by name.')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def guide(plan, primary_model):
    """Interactive investment wizard: analyze stocks, build plans, and paper trade.

    \b
    Steps through:
      1) Single stock: lookup -> analyze -> optional buy
      2) Multi-stock plan: choose goal -> set budget -> analyze all -> execute
      3) Saved plans: re-analyze with fresh data and optionally re-invest
    """
    # If a plan name is passed directly, skip the menu
    if plan:
        try:
            plan_data = _load_plan(plan)
        except ValueError:
            console.print(f"[red]Invalid plan name: {plan!r}[/red]")
            return
        if not plan_data:
            console.print(f"[red]Plan '{plan}' not found. Run 'buffet-bot plans' to list saved plans.[/red]")
            return
        _run_guide_plan(plan_data, primary_model)
        return

    account = trading_client.get_account()
    console.print(Panel(
        f"[bold green]Buffett Investment Guide[/bold green]\n\n"
        f"Account  Cash [bold]${float(account.cash):,.2f}[/bold]  |  "
        f"Buying Power [bold]${float(account.buying_power):,.2f}[/bold]\n\n"
        "[dim]Walk through analyzing stocks, build a multi-stock strategy,\n"
        "save recurring investment plans, and execute paper trades.[/dim]",
        border_style="blue",
    ))

    while True:
        console.print("\n[bold]What would you like to do?[/bold]")
        console.print("  [bold cyan]1[/bold cyan]  Analyze a single stock and optionally buy")
        console.print("  [bold cyan]2[/bold cyan]  Build a multi-stock investment plan")
        console.print("  [bold cyan]3[/bold cyan]  Load and run a saved investment plan")
        console.print("  [bold cyan]q[/bold cyan]  Quit\n")

        try:
            choice = Prompt.ask("[bold]Choice[/bold]", choices=['1', '2', '3', 'q'], default='1')
        except (KeyboardInterrupt, EOFError):
            break

        if choice == 'q':
            break
        elif choice == '1':
            _guide_single_stock(primary_model)
        elif choice == '2':
            _guide_build_plan(primary_model)
        elif choice == '3':
            _guide_load_plan(primary_model)

        if not click.confirm("\nReturn to main menu?", default=True):
            break

    console.print("[dim]Exiting Investment Guide. Good luck investing![/dim]")


@cli.command()
@click.option('--run',          'run_plan',      default=None, help='Execute a saved plan.')
@click.option('--delete',       'delete_plan',   default=None, help='Delete a saved plan.')
@click.option('--schedule',     'set_schedule',  default=None,
              type=click.Tuple([str, click.Choice(['daily', 'weekly', 'biweekly', 'monthly', 'off'])]),
              metavar='NAME FREQ',
              help='Attach a schedule to a plan (e.g. --schedule my-plan weekly).')
@click.option('--run-due',      'run_due',       is_flag=True, default=False,
              help='Run all scheduled plans that are currently due. Cron-friendly.')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def plans(run_plan, delete_plan, set_schedule, run_due, primary_model):
    """List, run, schedule, or delete saved investment plans.

    \b
    Examples:
      buffet-bot plans                              # list all saved plans
      buffet-bot plans --run my-plan               # re-analyze and execute a plan
      buffet-bot plans --schedule my-plan weekly   # run every 7 days
      buffet-bot plans --schedule my-plan off      # remove schedule
      buffet-bot plans --run-due                   # run all plans that are due (put in cron)
      buffet-bot plans --delete my-plan            # remove a plan
    """
    if delete_plan:
        try:
            path = _safe_plan_path(delete_plan)
        except ValueError:
            console.print(f"[red]Invalid plan name: {delete_plan!r}[/red]")
            return
        if path.exists():
            path.unlink()
            console.print(f"[green]Deleted plan '{delete_plan}'.[/green]")
        else:
            console.print(f"[red]Plan '{delete_plan}' not found.[/red]")
        return

    if set_schedule:
        plan_name, freq = set_schedule
        new_sched = None if freq == 'off' else freq
        if _set_plan_schedule(plan_name, new_sched):
            if new_sched:
                console.print(f"[green]Plan '{plan_name}' scheduled: {new_sched}.[/green]")
                console.print(
                    f"[dim]Cron example (daily 09:00): "
                    f"0 9 * * * buffet-bot plans --run-due[/dim]"
                )
            else:
                console.print(f"[yellow]Schedule removed from plan '{plan_name}'.[/yellow]")
        else:
            console.print(f"[red]Plan '{plan_name}' not found.[/red]")
        return

    if run_due:
        all_plans = _list_plans()
        due = [p for p in all_plans if _is_plan_due(p)]
        if not due:
            console.print("[dim]No scheduled plans are due right now.[/dim]")
            return
        console.print(f"[bold cyan]{len(due)} plan(s) due — running now...[/bold cyan]")
        for plan_data in due:
            name = plan_data.get('name', 'unnamed')
            console.print(Panel(
                f"[bold]{name}[/bold]  |  schedule: {plan_data.get('schedule')}",
                title="Running scheduled plan",
                border_style="cyan",
            ))
            _run_guide_plan(plan_data, primary_model)
            _mark_plan_ran(name)
            console.print(f"[green]✓ {name} complete.[/green]")
        return

    if run_plan:
        try:
            plan_data = _load_plan(run_plan)
        except ValueError:
            console.print(f"[red]Invalid plan name: {run_plan!r}[/red]")
            return
        if not plan_data:
            console.print(f"[red]Plan '{run_plan}' not found.[/red]")
            return
        _run_guide_plan(plan_data, primary_model)
        return

    saved = _list_plans()
    if not saved:
        console.print("[yellow]No saved plans. Run 'buffet-bot guide' to create one.[/yellow]")
        return

    table = Table(title="Saved Investment Plans", box=box.ROUNDED, header_style="bold blue")
    table.add_column("Name",     style="bold cyan")
    table.add_column("Goal")
    table.add_column("Tickers")
    table.add_column("Budget",   justify="right")
    table.add_column("Risk")
    table.add_column("Schedule", justify="center")
    table.add_column("Last Run",  style="dim")
    table.add_column("Updated",   style="dim")
    for p in saved:
        sched     = p.get('schedule') or '—'
        last_run  = (p.get('last_run_at') or '')[:10] or '—'
        due_badge = ' [bold yellow]DUE[/bold yellow]' if _is_plan_due(p) else ''
        table.add_row(
            p.get('name', '?'),
            p.get('goal', 'custom'),
            ', '.join(p.get('tickers', [])),
            f"${p.get('budget', 0):,.2f}",
            p.get('risk', 'medium'),
            f"{sched}{due_badge}",
            last_run,
            (p.get('updated_at') or p.get('created_at', ''))[:10],
        )
    console.print(table)
    console.print("\n[dim]Run:       buffet-bot plans --run <name>[/dim]")
    console.print("[dim]Schedule:  buffet-bot plans --schedule <name> daily|weekly|biweekly|monthly[/dim]")
    console.print("[dim]Run due:   buffet-bot plans --run-due  (put this in cron)[/dim]")
    console.print("[dim]Delete:    buffet-bot plans --delete <name>[/dim]")


# ── Automate ──────────────────────────────────────────────────────────────────

def _build_automate_tools(execute: bool, budget: float, primary_model: str, risk: str = 'medium', strategy: str = 'value') -> dict:
    """
    Return the tool registry for the automate agent loop.

    Each entry: {tool_name: {"description": str, "params": str, "fn": callable}}
    Budget is enforced via a mutable-list closure so spend accumulates across calls.
    """
    spent = [0.0]  # mutable closure for budget tracking

    DEFAULT_SCAN_TICKERS = [
        'AAPL', 'MSFT', 'GOOGL', 'BRK-B', 'JNJ', 'V', 'JPM', 'PG',
        'KO', 'WMT', 'ABBV', 'MRK', 'LLY', 'TMO', 'UNH', 'HD', 'AMZN', 'NVDA', 'META', 'TSLA',
    ]

    def scan_stocks(top=5):
        tickers = DEFAULT_SCAN_TICKERS
        results = {}
        with ThreadPoolExecutor(max_workers=min(len(tickers), 8)) as pool:
            futures = {pool.submit(get_buffett_metrics, t): t for t in tickers}
            for fut in as_completed(futures):
                t = futures[fut]
                try:
                    results[t] = fut.result()
                except Exception:
                    results[t] = {'score': 0}
        ranked = sorted(results.items(), key=lambda x: x[1].get('score', 0), reverse=True)
        return [{'ticker': t, 'score': m.get('score', 0)} for t, m in ranked[:int(top)]]

    def analyze_stock(ticker, risk=risk, strategy=strategy):
        ticker = ticker.upper()
        data = _run_analysis(ticker, risk, primary_model, strategy)
        realtime = data.get('realtime') or {}
        buffett  = data.get('buffett')  or {}
        best     = data.get('best_buy_resp') or {}
        return {
            'ticker':        ticker,
            'consensus':     data.get('consensus', 'HOLD'),
            'buffett_score': buffett.get('score', 0),
            'price':         realtime.get('price', 0),
            'reason':        best.get('reason', ''),
            'suggested_qty': best.get('qty', 0),
        }

    def get_portfolio():
        try:
            positions = trading_client.get_all_positions()
            return [
                {
                    'ticker':        p.symbol,
                    'qty':           p.qty,
                    'market_value':  float(p.market_value),
                    'unrealized_pl': float(p.unrealized_pl),
                }
                for p in positions
            ]
        except Exception as e:
            return {'error': str(e)}

    def get_account_status():
        try:
            acct = trading_client.get_account()
            return {
                'cash':          float(acct.cash),
                'buying_power':  float(acct.buying_power),
            }
        except Exception as e:
            return {'error': str(e)}

    def check_sell_candidates():
        try:
            positions = trading_client.get_all_positions()
            if not positions:
                return []
            tuples = _check_sell_signals(positions)
            return [
                {'ticker': pos.symbol, 'signals': sigs}
                for pos, sigs, _score, _rsi in tuples
                if sigs
            ]
        except Exception as e:
            return {'error': str(e)}

    def search_companies_tool(query):
        try:
            return list(search_companies(query, limit=10))
        except Exception as e:
            return {'error': str(e)}

    def browse_sector(sector):
        try:
            return list(list_companies(sector=sector, limit=20))
        except Exception as e:
            return {'error': str(e)}

    def buy_stock(ticker, qty):
        if not execute:
            return {'executed': False, 'reason': 'dry-run mode — pass --execute to place orders'}
        ticker = ticker.upper()
        qty    = int(qty)
        price  = (get_realtime_data(ticker) or {}).get('price', 0) or 0
        cost   = price * qty
        if spent[0] + cost > budget:
            return {
                'executed': False,
                'reason':   f'exceeds budget (${spent[0]:.2f} spent of ${budget:.2f})',
            }
        _place_order(ticker, {'qty': qty})
        spent[0] += cost
        return {
            'executed':       True,
            'ticker':         ticker,
            'qty':            qty,
            'estimated_cost': round(cost, 2),
            'total_spent':    round(spent[0], 2),
        }

    return {
        'scan_stocks': {
            'description': 'Scan default watchlist for Buffett-scored opportunities',
            'params':      'top=5',
            'fn':          scan_stocks,
        },
        'analyze_stock': {
            'description': 'Run full LLM analysis on a single ticker',
            'params':      'ticker, risk="medium", strategy="value"',
            'fn':          analyze_stock,
        },
        'get_portfolio': {
            'description': 'List current Alpaca paper portfolio positions',
            'params':      '',
            'fn':          get_portfolio,
        },
        'get_account_status': {
            'description': 'Get paper account cash and buying power',
            'params':      '',
            'fn':          get_account_status,
        },
        'check_sell_candidates': {
            'description': 'Check all positions for sell signals (stop/underperform/overbought)',
            'params':      '',
            'fn':          check_sell_candidates,
        },
        'search_companies': {
            'description': 'Search the company database by keyword or name',
            'params':      'query',
            'fn':          search_companies_tool,
        },
        'browse_sector': {
            'description': 'List companies in a sector (e.g. "Technology", "Healthcare")',
            'params':      'sector',
            'fn':          browse_sector,
        },
        'buy_stock': {
            'description': 'Place a paper market BUY order (respects --execute and --budget)',
            'params':      'ticker, qty',
            'fn':          buy_stock,
        },
    }


@cli.command()
@click.argument('goal', required=False, default=None)
@click.option('--execute', 'execute', is_flag=True, default=False,
              help='Allow paper trades. Default is dry-run.')
@click.option('--budget', default=500.0, show_default=True, type=float,
              help='Max capital to spend this session (paper $).')
@click.option('--max-steps', default=10, show_default=True, type=int,
              help='Max agent steps before stopping.')
@click.option('--model', 'primary_model', default=MODELS[0], show_default=True,
              type=click.Choice(MODELS),
              help='LLM to use as the agent brain.')
@click.option('--risk', default=None, type=click.Choice(['low', 'medium', 'high']),
              help='Risk appetite (low/medium/high). Prompted if omitted in wizard mode.')
@click.option('--strategy', default=None,
              type=click.Choice(['value', 'growth', 'dividend', 'turnaround']),
              help='Investing strategy. Prompted if omitted in wizard mode.')
def automate(goal, execute, budget, max_steps, primary_model, risk, strategy):
    """AI agent that autonomously chains buffet-bot tools to accomplish a goal.

    \b
    Examples:
      buffet-bot automate
      buffet-bot automate "find the top 3 value stocks"
      buffet-bot automate "invest $500 in the best stock" --execute --budget 500 --risk high
      buffet-bot automate "should I sell anything?" --max-steps 5
    """
    from rich.prompt import Prompt

    if not goal:
        console.print(Panel(
            "Let's set up your automated investing session.",
            title="[bold cyan]Buffet-Bot Automate[/bold cyan]",
            border_style="cyan",
        ))
        budget_str = Prompt.ask("How much do you want to invest?", default=f"{budget:.2f}")
        try:
            budget = float(budget_str.replace('$', '').replace(',', ''))
        except ValueError:
            console.print("[red]Invalid amount.[/red]")
            return
        if not risk:
            risk = Prompt.ask("Risk level", choices=['low', 'medium', 'high'], default='medium')
        if not strategy:
            strategy = Prompt.ask("Strategy", choices=['value', 'growth', 'dividend', 'turnaround'], default='value')
        if not execute:
            execute = click.confirm("Execute real paper trades?", default=False)
        goal = (
            f"Invest ${budget:.2f} using a {risk}-risk {strategy} strategy. "
            f"Scan for top candidates, analyze them, and buy the best opportunities."
        )
    else:
        if not risk:
            risk = 'medium'
        if not strategy:
            strategy = 'value'

    tools = _build_automate_tools(execute=execute, budget=budget, primary_model=primary_model,
                                   risk=risk, strategy=strategy)

    console.print(Panel(
        f"[bold]Goal:[/bold] {goal}\n"
        f"[bold]Budget:[/bold] ${budget:,.2f}  "
        f"[bold]Risk:[/bold] {risk}  "
        f"[bold]Strategy:[/bold] {strategy}  "
        f"[bold]Mode:[/bold] {'[green]EXECUTE[/green]' if execute else '[yellow]DRY RUN[/yellow]'}  "
        f"[bold]Max steps:[/bold] {max_steps}  "
        f"[bold]Model:[/bold] {primary_model}",
        title="[bold cyan]Buffet-Bot Automate[/bold cyan]",
        border_style="cyan",
    ))

    import ollama as _ollama
    result = run_agent_loop(
        goal=goal,
        tools=tools,
        model=primary_model,
        budget=budget,
        max_steps=max_steps,
        execute=execute,
        console=console,
        ollama_module=_ollama,
        risk=risk,
        strategy=strategy,
    )
    if result.get('timed_out'):
        console.print(
            f"[yellow]Agent stopped after {max_steps} steps without calling done().[/yellow]"
        )


@cli.command()
@click.option('--years',   default=10,  show_default=True, type=int,   help='Projection horizon.')
@click.option('--monthly', default=0.0, show_default=True, type=float, help='Additional monthly contribution ($).')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def forecast(years, monthly, primary_model):
    """AI-powered portfolio growth projection using Monte Carlo simulation.

    \b
    Fetches your current Alpaca positions, asks the AI for 5-year return
    estimates per holding, then runs 1,000 simulated market paths to show
    a probability cone (P10 / median / P90) over your chosen time horizon.
    """
    try:
        positions = trading_client.get_all_positions()
        account   = trading_client.get_account()
    except Exception as e:
        console.print(f"[red]Could not fetch portfolio: {e}[/red]")
        return

    if not positions:
        console.print("[yellow]No open positions found. Buy some stocks first, or use 'buffet-bot whatif' for a hypothetical.[/yellow]")
        return

    total_value = sum(float(p.market_value) for p in positions)
    cash        = float(account.cash)

    # Show current holdings
    tbl = Table(title="Current Holdings", box=box.ROUNDED, header_style="bold blue")
    tbl.add_column("Ticker", style="bold cyan")
    tbl.add_column("Qty",    justify="right")
    tbl.add_column("Price",  justify="right")
    tbl.add_column("Value",  justify="right")
    tbl.add_column("Weight", justify="right")
    for p in positions:
        mv = float(p.market_value)
        tbl.add_row(
            p.symbol,
            str(p.qty),
            f"${float(p.current_price):,.2f}",
            f"${mv:,.2f}",
            f"{mv / total_value:.1%}",
        )
    console.print(tbl)
    console.print(f"[dim]Total invested: ${total_value:,.2f}  |  Cash: ${cash:,.2f}[/dim]\n")

    console.print("[bold]Querying AI for per-holding return estimates...[/bold]")
    portfolio_est = _get_portfolio_expected_return(positions, primary_model)
    base_return   = portfolio_est['weighted_return']
    volatility    = portfolio_est['weighted_volatility']

    # Per-ticker table
    tbl2 = Table(title="AI Return Estimates", box=box.SIMPLE, header_style="bold dim")
    tbl2.add_column("Ticker", style="cyan")
    tbl2.add_column("Weight",    justify="right")
    tbl2.add_column("Est. Return", justify="right")
    tbl2.add_column("Est. Vol",    justify="right")
    tbl2.add_column("Rationale")
    for sym, d in portfolio_est['per_ticker'].items():
        tbl2.add_row(
            sym,
            f"{d['weight']:.1%}",
            f"{d['expected_annual_return_pct']:.1f}%",
            f"{d['volatility_pct']:.1f}%",
            d.get('rationale', '')[:60],
        )
    console.print(tbl2)
    console.print(f"\n[bold]Portfolio weighted return:[/bold] [cyan]{base_return:.1%}[/cyan]  "
                  f"[bold]Volatility:[/bold] [yellow]{volatility:.1%}[/yellow]\n")

    # Monte Carlo at checkpoints
    all_checkpoints = [y for y in [1, 2, 3, 5, 10, 20, 30] if y <= years]
    if years not in all_checkpoints:
        all_checkpoints.append(years)
    all_checkpoints.sort()

    console.print(f"[dim]Running Monte Carlo (1,000 simulations × {len(all_checkpoints)} checkpoints)...[/dim]")
    checkpoints = []
    for y in all_checkpoints:
        mc = _run_monte_carlo(base_return, volatility, total_value, monthly, y, n=1000)
        checkpoints.append((y, mc))

    _display_mc_chart(checkpoints, label=f"Portfolio Forecast — AI return {base_return:.1%}/yr")

    # Summary table
    tbl3 = Table(title="Probability Cone", box=box.ROUNDED, header_style="bold blue")
    tbl3.add_column("Year",         justify="right")
    tbl3.add_column("Bear (P10)",   justify="right", style="red")
    tbl3.add_column("Median",       justify="right", style="cyan")
    tbl3.add_column("Bull (P90)",   justify="right", style="green")
    tbl3.add_column("Deterministic", justify="right", style="dim")
    for y, mc in checkpoints:
        det = _calculate_future_value(total_value, monthly, base_return, y)
        tbl3.add_row(
            str(y),
            f"${mc['p10']:,.0f}",
            f"${mc['median']:,.0f}",
            f"${mc['p90']:,.0f}",
            f"${det:,.0f}",
        )
    console.print(tbl3)


@cli.command()
@click.option('--balance', default=None, type=float, help='Starting balance ($).')
@click.option('--monthly', default=None, type=float, help='Monthly contribution ($).')
@click.option('--years',   default=None, type=int,   help='Years to project.')
@click.option('--return-pct', 'custom_return', default=None, type=float,
              help='Override annual return %% (e.g. 12.5). Skips AI estimate.')
@click.option('--ticker',  default=None, help='Get AI return estimate for this ticker.')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def whatif(balance, monthly, years, custom_return, ticker, primary_model):
    """Interactive what-if investment calculator with AI return estimates.

    \b
    Examples:
      buffet-bot whatif --balance 10000 --monthly 500 --years 20 --return-pct 9
      buffet-bot whatif --balance 10000 --monthly 500 --years 20 --ticker AAPL
    """
    console.print(Panel(
        "[bold]What-If Investment Calculator[/bold]\n"
        "[dim]Model different scenarios with AI-powered or custom return estimates.[/dim]",
        border_style="blue",
    ))

    # Prompt for missing inputs
    if balance is None:
        balance = float(Prompt.ask("Starting balance ($)", default="10000").replace('$', '').replace(',', ''))
    if monthly is None:
        monthly = float(Prompt.ask("Monthly contribution ($)", default="500").replace('$', '').replace(',', ''))
    if years is None:
        years = int(Prompt.ask("Investment horizon (years)", default="10"))

    years   = max(1, min(years, 100))
    monthly = max(0.0, monthly)
    total_invested = balance + monthly * 12 * years

    # Determine return estimate
    ai_est = None
    if custom_return is not None:
        user_return = custom_return / 100
        volatility  = 0.18
        label       = f"Custom ({custom_return:.1f}%)"
    elif ticker:
        console.print(f"[dim]Querying AI for {ticker} return estimate...[/dim]")
        ai_est      = _get_ai_expected_return(ticker.upper(), primary_model)
        user_return = ai_est['expected_annual_return_pct'] / 100
        volatility  = ai_est['volatility_pct'] / 100
        label       = f"AI estimate for {ticker.upper()} ({ai_est['expected_annual_return_pct']:.1f}%)"
        console.print(f"[dim]Rationale: {ai_est.get('rationale', '')}[/dim]\n")
    else:
        pct_str     = Prompt.ask("Expected annual return %%", default="9.0")
        user_return = float(pct_str) / 100
        volatility  = 0.18
        label       = f"Custom ({float(pct_str):.1f}%)"

    sp500_return = 0.09

    # Calculate all three scenarios
    user_fv  = _calculate_future_value(balance, monthly, user_return,  years)
    sp500_fv = _calculate_future_value(balance, monthly, sp500_return, years)

    user_mc  = _run_monte_carlo(user_return,  volatility, balance, monthly, years)
    sp500_mc = _run_monte_carlo(sp500_return, 0.15,       balance, monthly, years)

    tbl = Table(title=f"What-If Projection — {years} Years", box=box.ROUNDED, header_style="bold blue")
    tbl.add_column("Scenario",     style="bold")
    tbl.add_column("Annual Return", justify="right")
    tbl.add_column("Total Invested", justify="right")
    tbl.add_column("Projected Value", justify="right")
    tbl.add_column("Gain",          justify="right")
    tbl.add_column("ROI",           justify="right")
    tbl.add_column("P10 (Bear)",    justify="right", style="red")
    tbl.add_column("P90 (Bull)",    justify="right", style="green")

    def _row(name, fv, ret, mc, color='white'):
        gain = fv - total_invested
        roi  = (gain / total_invested * 100) if total_invested else 0
        tbl.add_row(
            f"[{color}]{name}[/{color}]",
            f"[{color}]{ret:.1%}[/{color}]",
            f"${total_invested:,.0f}",
            f"[bold {color}]${fv:,.0f}[/bold {color}]",
            f"[{color}]${gain:,.0f}[/{color}]",
            f"[{color}]{roi:.0f}%[/{color}]",
            f"${mc['p10']:,.0f}",
            f"${mc['p90']:,.0f}",
        )

    _row(label,  user_fv,  user_return,  user_mc,  'cyan')
    _row("S&P 500 (9%)", sp500_fv, sp500_return, sp500_mc, 'green')

    console.print(tbl)

    # Probability cone chart for user scenario
    checkpoints = [(y, _run_monte_carlo(user_return, volatility, balance, monthly, y))
                   for y in sorted({1, 5, min(10, years), years}) if y > 0]
    _display_mc_chart(checkpoints, label=f"What-If: {label}")


@cli.command()
@click.option('--balance', default=None,  type=float, help='Starting balance ($).')
@click.option('--monthly', default=None,  type=float, help='Monthly contribution ($).')
@click.option('--years',   default=None,  type=int,   help='Projection horizon (years).')
@click.option('--ticker',  default=None,  help='Ticker for AI return estimate (optional).')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def scenarios(balance, monthly, years, ticker, primary_model):
    """Side-by-side projection across 5 scenarios: conservative to aggressive.

    \b
    Examples:
      buffet-bot scenarios --balance 10000 --monthly 500 --years 20
      buffet-bot scenarios --balance 10000 --monthly 500 --years 20 --ticker MSFT
    """
    console.print(Panel(
        "[bold]Scenario Comparison[/bold]\n"
        "[dim]Compare Conservative / AI Balanced / Aggressive / Bear / S&P 500 projections.[/dim]",
        border_style="blue",
    ))

    if balance is None:
        balance = float(Prompt.ask("Starting balance ($)", default="10000").replace('$', '').replace(',', ''))
    if monthly is None:
        monthly = float(Prompt.ask("Monthly contribution ($)", default="500").replace('$', '').replace(',', ''))
    if years is None:
        years = int(Prompt.ask("Projection horizon (years)", default="10"))

    years   = max(1, min(years, 100))
    monthly = max(0.0, monthly)

    # Get AI base if ticker provided
    ai_return, ai_vol = 0.08, 0.18
    if ticker:
        console.print(f"[dim]Querying AI for {ticker.upper()} return estimate...[/dim]")
        est = _get_ai_expected_return(ticker.upper(), primary_model)
        ai_return = est['expected_annual_return_pct'] / 100
        ai_vol    = est['volatility_pct'] / 100
        console.print(f"[dim]AI estimate: {ai_return:.1%}/yr  Volatility: {ai_vol:.1%}[/dim]\n")

    scenario_defs = [
        ('Conservative (6%)',          0.06,             0.10, 'dim'),
        (f'AI Balanced ({ai_return:.0%})',  ai_return,    ai_vol, 'cyan'),
        (f'Aggressive ({ai_return*1.4:.0%})', min(ai_return*1.4, 0.30), ai_vol*1.2, 'yellow'),
        (f'Bear ({ai_return*0.5:.0%})',    ai_return*0.5, ai_vol*1.5, 'red'),
        ('S&P 500 (9%)',                0.09,             0.15, 'green'),
    ]

    year_marks = sorted({1, 5, 10, years}) if years > 5 else sorted({1, 3, years})

    # Header row: scenario names as columns
    tbl = Table(title=f"Scenario Comparison — ${balance:,.0f} start, ${monthly:,.0f}/mo, {years}yr",
                box=box.ROUNDED, header_style="bold blue")
    tbl.add_column("Year", justify="right")
    for name, _, _, color in scenario_defs:
        tbl.add_column(name, justify="right", style=color)

    for y in year_marks:
        row = [str(y)]
        for _, ret, _, _ in scenario_defs:
            fv = _calculate_future_value(balance, monthly, ret, y)
            row.append(f"${fv:,.0f}")
        tbl.add_row(*row)

    # Add MC P10/P90 rows for the final year
    tbl.add_row(*(['P10 (Bear)'] + [
        f"${_run_monte_carlo(ret, vol, balance, monthly, years, n=500)['p10']:,.0f}"
        for _, ret, vol, _ in scenario_defs
    ]))
    tbl.add_row(*(['P90 (Bull)'] + [
        f"${_run_monte_carlo(ret, vol, balance, monthly, years, n=500)['p90']:,.0f}"
        for _, ret, vol, _ in scenario_defs
    ]))

    console.print(tbl)

    total_invested = balance + monthly * 12 * years
    console.print(f"\n[dim]Total invested over {years} years: [bold]${total_invested:,.0f}[/bold][/dim]")


@cli.command()
@click.option('--balance', default=None,  type=float, help='Current savings ($).')
@click.option('--monthly', default=None,  type=float, help='Monthly contribution ($).')
@click.option('--return-pct', 'annual_return', default=9.0, show_default=True, type=float,
              help='Expected annual return %%.')
def milestones(balance, monthly, annual_return):
    """Show projected dates for hitting key financial milestones.

    \b
    Example:
      buffet-bot milestones --balance 15000 --monthly 800 --return-pct 10
    """
    if balance is None:
        balance = float(Prompt.ask("Current savings ($)", default="10000").replace('$', '').replace(',', ''))
    if monthly is None:
        monthly = float(Prompt.ask("Monthly contribution ($)", default="500").replace('$', '').replace(',', ''))

    annual_return_dec = annual_return / 100
    MILESTONES = [25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000]

    tbl = Table(
        title=f"Milestones — ${balance:,.0f} start, ${monthly:,.0f}/mo, {annual_return:.1f}%/yr",
        box=box.ROUNDED, header_style="bold blue",
    )
    tbl.add_column("Milestone",       style="bold cyan")
    tbl.add_column("Already there?",  justify="center")
    tbl.add_column("Years away",      justify="right")
    tbl.add_column("Est. Date",       justify="right")
    tbl.add_column("Total Invested",  justify="right", style="dim")

    for target in MILESTONES:
        if balance >= target:
            tbl.add_row(f"${target:,.0f}", "[green]Yes[/green]", "—", "—", "—")
            continue
        yrs = _years_to_reach(target, balance, monthly, annual_return_dec)
        if yrs is None:
            tbl.add_row(f"${target:,.0f}", "No", "100+ yrs", "Never", "—")
        else:
            eta = datetime.now() + timedelta(days=yrs * 365.25)
            invested = balance + monthly * 12 * yrs
            tbl.add_row(
                f"${target:,.0f}",
                "No",
                f"{yrs:.1f} yrs",
                eta.strftime("%b %Y"),
                f"${invested:,.0f}",
            )

    console.print(tbl)


@cli.command()
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--period',  default=1.0,     show_default=True, type=float, help='Period in years.')
@click.option('--capital', default=10000.0, show_default=True, type=float, help='Starting capital ($).')
@click.option('--compare/--no-compare', default=True, show_default=True,
              help='Compare against SPY buy-and-hold benchmark.')
def backtest(ticker, period, capital, compare):
    """Backtest RSI strategy on a ticker: buffet-bot backtest AAPL --period 2 --capital 10000"""
    ticker = ticker.upper()
    console.print(Panel(
        f"[bold]{ticker}[/bold]  |  Period: {period:.1f}yr  |  Capital: ${capital:,.0f}",
        title="Backtesting", border_style="blue"))

    with console.status("[bold blue]Running backtest...[/bold blue]"):
        metrics, equity_curve, dates, spy_metrics = _run_backtest(
            ticker, period, capital, compare_spy=compare)

    if metrics is None:
        console.print("[red]Backtest failed — insufficient data (need ≥60 bars).[/red]")
        return

    tbl = Table(title="Backtest Results", box=box.ROUNDED, header_style="bold blue")
    tbl.add_column("Metric", style="bold")
    tbl.add_column(ticker, justify="right")
    if spy_metrics:
        tbl.add_column("SPY (B&H)", justify="right", style="dim")

    def _mrow(label, key, fmt_fn, color_fn):
        val     = metrics.get(key)
        val_str = fmt_fn(val) if val is not None else "—"
        color   = color_fn(val) if val is not None else "white"
        row     = [label, f"[{color}]{val_str}[/{color}]"]
        if spy_metrics:
            sv      = spy_metrics.get(key)
            row.append(fmt_fn(sv) if sv is not None else "—")
        tbl.add_row(*row)

    _mrow("Total Return",  "total_return",  lambda v: f"{v:.1%}",
          lambda v: "green" if v > 0 else "red")
    _mrow("CAGR",          "cagr",          lambda v: f"{v:.1%}",
          lambda v: "green" if v > 0.09 else "yellow" if v > 0 else "red")
    _mrow("Sharpe Ratio",  "sharpe",        lambda v: f"{v:.2f}",
          lambda v: "green" if v > 1 else "yellow" if v > 0.5 else "red")
    _mrow("Max Drawdown",  "max_drawdown",  lambda v: f"{v:.1%}",
          lambda v: "green" if v < 0.20 else "yellow" if v < 0.30 else "red")
    _mrow("Win Rate",      "win_rate",      lambda v: f"{v:.1%}",
          lambda v: "green" if v >= 0.5 else "yellow")
    _mrow("Avg Win ($)",   "avg_win",       lambda v: f"${v:.2f}",  lambda v: "green")
    _mrow("Avg Loss ($)",  "avg_loss",      lambda v: f"${v:.2f}",  lambda v: "red")
    _mrow("Profit Factor", "profit_factor",
          lambda v: f"{v:.2f}" if v != float('inf') else "∞",
          lambda v: "green" if v >= 1.5 else "yellow" if v >= 1 else "red")
    _mrow("# Trades",      "n_trades",      lambda v: str(int(v)),  lambda v: "white")

    console.print(tbl)

    if equity_curve:
        step = max(1, len(equity_curve) // 60)
        xs   = list(range(0, len(equity_curve), step))
        ys   = [equity_curve[i] for i in xs]
        try:
            plt.clf()
            plt.plot(xs, ys, color='cyan', label=ticker)
            plt.title(f"{ticker} Backtest — Equity Curve")
            plt.xlabel("Trading Days")
            plt.ylabel("Value ($)")
            plt.show()
        except Exception as e:
            console.print(f"[dim]Chart unavailable: {e}[/dim]")


@cli.command()
def correlate():
    """Correlation matrix for current portfolio: buffet-bot correlate"""
    try:
        positions = trading_client.get_all_positions()
    except Exception as e:
        console.print(f"[red]Could not fetch positions: {e}[/red]")
        return

    if len(positions) < 2:
        console.print("[yellow]Need at least 2 open positions for correlation analysis.[/yellow]")
        return

    tickers = [p.symbol for p in positions]
    console.print(f"[dim]Fetching 6-month returns for: {', '.join(tickers)}...[/dim]")

    try:
        raw = yf.download(tickers, period='6mo', auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            close_data = raw['Close']
        else:
            close_data = raw[['Close']] if len(tickers) == 1 else raw
        returns = close_data.pct_change().dropna()
        corr    = returns.corr()
    except Exception as e:
        console.print(f"[red]Correlation error: {e}[/red]")
        return

    valid_tickers = [t for t in tickers if t in corr.columns]

    tbl = Table(title="Correlation Matrix (6-month daily returns)",
                box=box.ROUNDED, header_style="bold blue")
    tbl.add_column("", style="bold cyan")
    for t in valid_tickers:
        tbl.add_column(t, justify="right")

    for t1 in valid_tickers:
        row = [t1]
        for t2 in valid_tickers:
            if t1 == t2:
                row.append("[dim]1.00[/dim]")
            else:
                val   = float(corr.loc[t1, t2])
                color = "green" if abs(val) < 0.3 else "yellow" if abs(val) < 0.6 else "red"
                row.append(f"[{color}]{val:.2f}[/{color}]")
        tbl.add_row(*row)

    console.print(tbl)

    pairs = list(itertools.combinations(valid_tickers, 2))
    if pairs:
        avg_abs   = float(np.mean([abs(corr.loc[t1, t2]) for t1, t2 in pairs]))
        diversity = 1 - avg_abs
        color     = "green" if diversity > 0.7 else "yellow" if diversity > 0.4 else "red"
        console.print(
            f"\n[bold]Diversity Score:[/bold] [{color}]{diversity:.2f}[/{color}]  "
            "[dim](1.0 = fully uncorrelated  0.0 = identical moves)[/dim]")

    _show_sector_table(valid_tickers)


@cli.command('check-sells')
@click.option('--execute', is_flag=True, default=False,
              help='Sell positions flagged STOP or THESIS_BROKEN.')
@click.option('--tlh-threshold', 'tlh_pct', default=5.0, show_default=True, type=float,
              help='Minimum loss % to flag a TAX_LOSS harvest candidate (default 5.0).')
def check_sells(execute, tlh_pct):
    """Check open positions for sell signals: buffet-bot check-sells [--execute]"""
    try:
        positions = trading_client.get_all_positions()
    except Exception as e:
        console.print(f"[red]Could not fetch positions: {e}[/red]")
        return

    if not positions:
        console.print("[yellow]No open positions.[/yellow]")
        return

    console.print(f"[dim]Checking {len(positions)} position(s) for sell signals...[/dim]")
    with console.status("[bold blue]Analyzing signals...[/bold blue]"):
        results = _check_sell_signals(positions, tlh_pct=tlh_pct)

    tbl = Table(title="Sell Signal Analysis", box=box.ROUNDED, header_style="bold blue")
    tbl.add_column("Ticker",  style="bold cyan")
    tbl.add_column("Entry",   justify="right")
    tbl.add_column("Current", justify="right")
    tbl.add_column("P&L%",    justify="right")
    tbl.add_column("B.Score", justify="right")
    tbl.add_column("RSI",     justify="right")
    tbl.add_column("Signals")
    tbl.add_column("Rec.")

    sell_flagged = []
    for pos, signals, b_score, rsi_val in results:
        try:
            entry   = float(pos.avg_entry_price)
            current = float(pos.current_price)
            pnl_pct = (current - entry) / entry * 100 if entry else 0.0
        except Exception:
            entry = current = pnl_pct = 0.0

        pnl_color = "green" if pnl_pct >= 0 else "red"
        s_color   = _score_color(b_score)
        sig_text  = ", ".join(signals) if signals else "—"

        if {'STOP', 'THESIS_BROKEN'} & set(signals):
            rec = "[bold red]SELL[/bold red]"
            sell_flagged.append(pos)
        elif signals:
            rec = "[bold yellow]REVIEW[/bold yellow]"
        else:
            rec = "[bold green]HOLD[/bold green]"

        tbl.add_row(
            pos.symbol,
            f"${entry:.2f}",
            f"${current:.2f}",
            f"[{pnl_color}]{pnl_pct:+.1f}%[/{pnl_color}]",
            f"[{s_color}]{b_score}[/{s_color}]",
            f"{rsi_val:.1f}" if rsi_val else "—",
            sig_text,
            rec,
        )

    console.print(tbl)

    has_tlh = any('TAX_LOSS' in sigs for _, sigs, _, _ in results)
    if has_tlh:
        console.print(
            f"[dim]⚠  TAX_LOSS candidates flagged at ≥{tlh_pct:.0f}% unrealised loss. "
            "This is a simulated signal only — consult a qualified tax advisor before "
            "harvesting losses.[/dim]"
        )

    if execute and sell_flagged:
        console.print(f"\n[bold red]Selling: {', '.join(p.symbol for p in sell_flagged)}[/bold red]")
        if click.confirm("Confirm sells? (Paper)"):
            for pos in sell_flagged:
                try:
                    order  = MarketOrderRequest(
                        symbol=pos.symbol,
                        qty=int(float(pos.qty)),
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                    )
                    result = trading_client.submit_order(order)
                    console.print(f"[green]SELL {pos.symbol} submitted: {result.id}[/green]")
                except Exception as e:
                    console.print(f"[red]Error selling {pos.symbol}: {e}[/red]")
    elif sell_flagged and not execute:
        console.print("\n[dim]To execute these sells: buffet-bot check-sells --execute[/dim]")


@cli.command()
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--interval', default='1m', type=click.Choice(['1m', '5m', '15m']),
              show_default=True, help='Refresh interval.')
def stream(ticker, interval):
    """Live price stream with rolling chart: buffet-bot stream AAPL --interval 1m"""
    sleep_map = {'1m': 60, '5m': 300, '15m': 900}
    sleep_secs = sleep_map[interval]
    ticker     = ticker.upper()
    prices     = deque(maxlen=60)

    console.print(Panel(
        f"Streaming [bold]{ticker}[/bold] — {interval} interval\n"
        "[dim]Press Ctrl+C to stop[/dim]",
        border_style="blue"))

    try:
        while True:
            data = get_realtime_data(ticker)
            if data:
                prices.append(data['price'])

            console.clear()
            if data:
                sign      = '+' if data['change_pct'] >= 0 else ''
                pct_color = 'green' if data['change_pct'] >= 0 else 'red'
                console.print(Panel(
                    f"Price:  [bold]${data['price']:.2f}[/bold]  "
                    f"[{pct_color}]{sign}{data['change_pct']}%[/{pct_color}]\n"
                    f"High: ${data['high']:.2f}  Low: ${data['low']:.2f}  "
                    f"Vol: {data['volume']:,}\n"
                    f"Updated: {datetime.now().strftime('%H:%M:%S')}",
                    title=f"[bold]{ticker}[/bold] Live Stream",
                    border_style="green",
                ))

            if len(prices) >= 2:
                try:
                    plt.clf()
                    plt.plot(list(range(len(prices))), list(prices), color='cyan')
                    plt.title(f"{ticker} — Last {len(prices)} ticks")
                    plt.xlabel("Ticks")
                    plt.ylabel("Price ($)")
                    plt.show()
                except Exception:
                    pass

            time.sleep(sleep_secs)
    except KeyboardInterrupt:
        console.print("\n[dim]Stream stopped.[/dim]")


@cli.command()
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--period', default='1mo', type=click.Choice(['1d', '5d', '1mo']),
              show_default=True, help='Chart period.')
@click.option('--save', 'save_path', default=None, metavar='PATH',
              help='PNG output path (default: <TICKER>_chart.png).')
def chart(ticker, period, save_path):
    """Candlestick chart with SMA overlays: buffet-bot chart AAPL --period 1mo"""
    ticker    = ticker.upper()
    save_path = save_path or f"{ticker}_chart.png"
    interval  = {'1d': '5m', '5d': '30m', '1mo': '1d'}[period]

    console.print(f"[dim]Fetching {ticker} OHLCV ({period}, {interval} bars)...[/dim]")
    try:
        data = yf.download(ticker, period=period, interval=interval,
                           auto_adjust=True, progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.droplevel(1)
        if data.empty:
            console.print("[red]No data returned.[/red]")
            return
        close = data['Close'].squeeze().astype(float)
    except Exception as e:
        console.print(f"[red]Data error: {e}[/red]")
        return

    sma20       = close.rolling(20).mean()
    sma50       = close.rolling(50).mean()
    xs          = list(range(len(close)))
    sma20_pairs = [(i, float(v)) for i, v in enumerate(sma20) if not pd.isna(v)]
    sma50_pairs = [(i, float(v)) for i, v in enumerate(sma50) if not pd.isna(v)]

    try:
        plt.clf()
        plt.plot(xs, close.tolist(), color='cyan', label='Close')
        if sma20_pairs:
            plt.plot([p[0] for p in sma20_pairs], [p[1] for p in sma20_pairs],
                     color='yellow', label='SMA20')
        if sma50_pairs:
            plt.plot([p[0] for p in sma50_pairs], [p[1] for p in sma50_pairs],
                     color='green', label='SMA50')
        plt.title(f"{ticker} — {period} ({interval} bars)")
        plt.xlabel("Bars")
        plt.ylabel("Price ($)")
        plt.show()
    except Exception as e:
        console.print(f"[dim]Terminal chart unavailable: {e}[/dim]")

    try:
        import mplfinance as mpf
        ohlcv = data[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
        mpf.plot(ohlcv, type='candle', volume=True, mav=(20, 50),
                 savefig=save_path, style='charles')
        console.print(f"[green]Chart saved:[/green] {save_path}")
    except ImportError:
        console.print("[yellow]PNG export skipped — install with: pip install mplfinance[/yellow]")
    except Exception as e:
        console.print(f"[red]PNG export error: {e}[/red]")


@cli.command()
@click.argument('tickers', nargs=-1)
@click.option('--interval', default=60, show_default=True, type=int,
              help='Refresh interval in seconds.')
def dashboard(tickers, interval):
    """Live multi-ticker price dashboard: buffet-bot dashboard AAPL MSFT GOOGL TSLA"""
    if not tickers:
        tickers = ('AAPL', 'MSFT', 'GOOGL', 'TSLA')
    tickers = tuple(t.upper() for t in tickers)

    console.print(Panel(
        f"Watching [bold cyan]{len(tickers)}[/bold cyan] ticker(s): "
        f"[bold]{', '.join(tickers)}[/bold]\n"
        f"[dim]Refreshing every {interval}s — Ctrl+C to exit[/dim]",
        title="[bold]Live Dashboard[/bold]",
        border_style="blue"))

    try:
        while True:
            with console.status("[dim]Fetching prices...[/dim]", spinner="dots"):
                rows = {t: get_realtime_data(t) for t in tickers}
            console.clear()

            now_str  = datetime.now().strftime('%H:%M:%S')
            next_str = (datetime.now() + timedelta(seconds=interval)).strftime('%H:%M:%S')

            tbl = Table(
                title=f"[bold]Live Dashboard[/bold]  [dim]{now_str}[/dim]",
                box=box.ROUNDED, header_style="bold blue",
            )
            tbl.add_column("Ticker",  style="bold cyan",  min_width=8,  no_wrap=True)
            tbl.add_column("Price",   justify="right",    min_width=10)
            tbl.add_column("Change%", justify="right",    min_width=10)
            tbl.add_column("Open",    justify="right",    min_width=10)
            tbl.add_column("High",    justify="right",    min_width=10)
            tbl.add_column("Low",     justify="right",    min_width=10)
            tbl.add_column("Volume",  justify="right",    min_width=13)
            tbl.add_column("Source",  style="dim",        min_width=9,  no_wrap=True)

            for t in tickers:
                d   = rows.get(t) or {}
                chg = d.get('change_pct', 0.0)
                sign = '+' if chg >= 0 else ''
                clr  = _change_color(chg)
                tbl.add_row(
                    t,
                    f"${d['price']:.2f}"                       if 'price'      in d else "—",
                    f"[{clr}]{sign}{chg:.2f}%[/{clr}]"         if 'change_pct' in d else "—",
                    f"${d['open']:.2f}"                        if 'open'       in d else "—",
                    f"${d['high']:.2f}"                        if 'high'       in d else "—",
                    f"${d['low']:.2f}"                         if 'low'        in d else "—",
                    f"{d['volume']:,}"                         if 'volume'     in d else "—",
                    d.get('source', '—'),
                )

            console.print(tbl)
            console.print(
                f"[dim]  ■ ≥+2%  [bold bright_green]bright green[/bold bright_green]"
                f"  ■ 0–+2%  [green]green[/green]"
                f"  ■ 0–−2%  [red]red[/red]"
                f"  ■ <−2%  [bold bright_red]bright red[/bold bright_red]"
                f"    Next refresh: {next_str}[/dim]"
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard stopped.[/dim]")


# ── New v0.4.0 commands ───────────────────────────────────────────────────────

@cli.command()
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--days', default=90, show_default=True, type=int,
              help='Look-back window for congressional trades (days).')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def news(ticker, days, primary_model):
    """News + congressional trade intelligence: buffet-bot news AAPL --days 60"""
    ticker = ticker.upper()
    console.print(Panel(
        f"[bold]{ticker}[/bold]  |  Congressional trade window: [cyan]{days}d[/cyan]",
        title="News & Politician Intelligence", border_style="blue",
    ))

    # 1) Alpaca news headlines (reuse existing helper)
    recent_news = get_recent_news(ticker, limit=8)
    _print_live_market(ticker, get_realtime_data(ticker), recent_news)

    # 2) Short interest + beta from yfinance
    try:
        info       = yf.Ticker(ticker).info
        beta       = info.get("beta")
        short_pct  = info.get("shortPercentOfFloat")
        short_ratio = info.get("shortRatio")
        shares_short = info.get("sharesShort")

        rows = []
        if beta       is not None: rows.append(f"Beta:               [yellow]{beta:.2f}[/yellow]")
        if short_pct  is not None: rows.append(f"Short % of Float:   [yellow]{short_pct*100:.1f}%[/yellow]")
        if short_ratio is not None: rows.append(f"Short Ratio (days): [yellow]{short_ratio:.1f}[/yellow]")
        if shares_short is not None: rows.append(f"Shares Short:       [dim]{shares_short:,}[/dim]")

        if rows:
            console.print(Panel("\n".join(rows), title="Short Interest & Beta", border_style="dim"))
    except Exception:
        pass

    # 3) Congressional trades (House Stock Watcher + FMP merged)
    console.print(f"\n[bold]Congressional Trades — last {days} days[/bold]")
    with console.status("[dim]Fetching congressional data...[/dim]"):
        house_trades = fetch_house_trades(ticker=ticker, days=days)
        fmp_trades   = fetch_fmp_trades(ticker=ticker)
    all_trades = merge_deduplicate(house_trades, fmp_trades)
    display_politician_trades(all_trades, ticker, console)

    if not os.getenv("FMP_API_KEY"):
        console.print(
            "[dim]Tip: Set FMP_API_KEY in .env for Senate trade data "
            "(free at financialmodelingprep.com)[/dim]"
        )

    # 4) AI sentiment summary combining news + politician activity
    if recent_news or all_trades:
        news_text = "\n".join(
            f"- [{n['published_at']}] {n['headline']}" for n in recent_news
        ) if recent_news else "No recent news."

        pol_buys  = sum(1 for t in all_trades if t["action"] == "Purchase")
        pol_sells = sum(1 for t in all_trades if t["action"] == "Sale")
        pol_text  = (
            f"{len(all_trades)} congressional trades in the last {days} days: "
            f"{pol_buys} purchases, {pol_sells} sales."
            if all_trades else "No congressional trades found."
        )

        ai_prompt = (
            f"Summarize the investment sentiment for {ticker} based on:\n\n"
            f"NEWS:\n{news_text}\n\n"
            f"CONGRESSIONAL ACTIVITY:\n{pol_text}\n\n"
            "In 3-4 sentences, assess: is there bullish or bearish signal from insider/politician "
            "activity and news? What should a retail investor watch for?"
        )
        with console.status("[dim]Querying AI for sentiment summary...[/dim]"):
            try:
                resp = ollama.chat(
                    model=primary_model,
                    messages=[{"role": "user", "content": ai_prompt}],
                    options={"temperature": 0.4},
                )
                summary = resp["message"]["content"].strip()
                color = MODEL_COLORS.get(primary_model, "white")
                console.print(Panel(
                    summary,
                    title=f"[bold {color}]AI Sentiment Summary ({primary_model})[/bold {color}]",
                    border_style=color,
                ))
            except Exception as e:
                console.print(f"[dim]AI summary unavailable: {e}[/dim]")


@cli.command()
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--days',  default=90, show_default=True, type=int,
              help='Look-back window for Form 4 filings (days).')
@click.option('--limit', default=15, show_default=True, type=int,
              help='Max transactions to display.')
def insiders(ticker, days, limit):
    """SEC Form 4 insider trades for a stock: buffet-bot insiders AAPL"""
    ticker = ticker.upper()
    console.print(Panel(
        f"[bold]{ticker}[/bold]  |  Window: [cyan]{days}d[/cyan]  |  "
        f"Max rows: [cyan]{limit}[/cyan]",
        title="SEC EDGAR Form 4 — Insider Transactions",
        border_style="blue",
    ))
    with console.status(
        f"[dim]Fetching SEC EDGAR Form 4 filings for {ticker}...[/dim]",
        spinner="dots",
    ):
        txns = fetch_insider_transactions(ticker, days=days, limit=limit)
    display_insider_table(ticker, txns, console)


@cli.command()
@click.argument('symbol', required=False, default=None)
@click.option('--dry-run/--execute', default=True)
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def crypto(symbol, dry_run, primary_model):
    """Crypto dashboard or single analysis: buffet-bot crypto / buffet-bot crypto BTC/USD"""
    if symbol:
        # Single crypto analysis
        _analyze_crypto(symbol.upper(), dry_run, primary_model)
        return

    # No symbol → show dashboard of all supported crypto
    console.print(Panel(
        "[bold]Crypto Dashboard[/bold]  —  Alpaca paper data\n"
        f"[dim]{', '.join(CRYPTO_SYMBOLS)}[/dim]",
        border_style="yellow",
    ))

    tbl = Table(
        title="Crypto Live Quotes",
        box=box.ROUNDED,
        header_style="bold yellow",
    )
    tbl.add_column("Symbol",     style="bold cyan")
    tbl.add_column("Mid Price",  justify="right")
    tbl.add_column("30d Return", justify="right")
    tbl.add_column("Ann. Vol%",  justify="right")
    tbl.add_column("Max DD%",    justify="right")

    with console.status("[dim]Fetching crypto data...[/dim]"):
        with ThreadPoolExecutor(max_workers=8) as pool:
            quote_futures = {pool.submit(get_crypto_quote, s): s for s in CRYPTO_SYMBOLS}
            vol_futures   = {pool.submit(get_crypto_volatility, s): s for s in CRYPTO_SYMBOLS}

            quotes = {}
            for fut in as_completed(quote_futures):
                s = quote_futures[fut]
                try:
                    quotes[s] = fut.result()
                except Exception:
                    quotes[s] = {}

            vols = {}
            for fut in as_completed(vol_futures):
                s = vol_futures[fut]
                try:
                    vols[s] = fut.result()
                except Exception:
                    vols[s] = {}

    for sym in CRYPTO_SYMBOLS:
        q = quotes.get(sym, {})
        v = vols.get(sym, {})
        mid = q.get("mid")
        ret = v.get("return_30d")
        avol = v.get("vol_30d_annualized")
        dd   = v.get("max_drawdown")

        ret_color = "green" if (ret or 0) >= 0 else "red"
        tbl.add_row(
            sym,
            f"${mid:,.4f}"                              if mid  is not None else "—",
            f"[{ret_color}]{ret:+.1f}%[/{ret_color}]"  if ret  is not None else "—",
            f"{avol:.0f}%"                              if avol is not None else "—",
            f"[red]{dd:.1f}%[/red]"                    if dd   is not None else "—",
        )

    console.print(tbl)
    console.print("[dim]Run 'buffet-bot crypto BTC/USD' for a full analysis.[/dim]")


@cli.command()
@click.option('--n', default=10, show_default=True, type=int,
              help='Number of top volatile tickers to show.')
@click.option('--universe', multiple=True, metavar='TICKER',
              help='Custom tickers to screen (repeatable). Uses built-in universe if omitted.')
def volatile(n, universe):
    """High-beta small-cap volatility scanner: buffet-bot volatile --n 15"""
    custom = list(t.upper() for t in universe) if universe else None
    label  = f"custom ({len(custom)} tickers)" if custom else f"built-in ({len(VOLATILE_UNIVERSE)} tickers)"
    console.print(Panel(
        f"Scanning [bold]{label}[/bold], showing top [bold]{n}[/bold] by volatility score",
        title="Volatile Scanner", border_style="magenta",
    ))

    with console.status("[bold magenta]Scoring tickers concurrently...[/bold magenta]"):
        results = scan_volatile(universe=custom, n=n)

    display_volatile_table(results, console)
    console.print(
        "[dim]Score weights: Beta 30 | Mkt Cap <$2B 25 | Short% 25 | 30d Vol 20[/dim]"
    )


@cli.command()
@click.option('--execute', is_flag=True, default=False,
              help='Place paper orders to rebalance (buy only).')
@click.option('--include-cash', is_flag=True, default=False,
              help='Include available cash when computing target allocation.')
def rebalance(execute, include_cash):
    """Compare portfolio to equal-weight target and suggest trades: buffet-bot rebalance"""
    try:
        positions = trading_client.get_all_positions()
        account   = trading_client.get_account()
    except Exception as e:
        console.print(f"[red]Could not fetch portfolio: {e}[/red]")
        return

    if not positions:
        console.print("[yellow]No open positions to rebalance.[/yellow]")
        return

    cash = float(account.cash)
    position_value = sum(float(p.market_value) for p in positions)
    total_value = position_value + cash if include_cash else position_value

    if total_value <= 0:
        console.print("[red]Portfolio value is zero.[/red]")
        return

    n = len(positions)
    target_pct = 1.0 / n
    target_value = total_value * target_pct

    table = Table(title="Rebalance Analysis (Equal Weight)", box=box.ROUNDED,
                  header_style="bold cyan")
    table.add_column("Ticker",   style="bold", no_wrap=True)
    table.add_column("Current $",  justify="right")
    table.add_column("Current %",  justify="right")
    table.add_column("Target %",   justify="right")
    table.add_column("Diff %",     justify="right")
    table.add_column("Action")
    table.add_column("Shares",     justify="right")

    buys = []
    for pos in sorted(positions, key=lambda p: float(p.market_value), reverse=True):
        symbol     = pos.symbol
        mkt_val    = float(pos.market_value)
        price      = float(pos.current_price)
        actual_pct = mkt_val / total_value
        diff_pct   = actual_pct - target_pct
        diff_val   = mkt_val - target_value
        shares_delta = int(abs(diff_val) / price) if price > 0 else 0

        if abs(diff_pct) < 0.01:          # within 1% — no action needed
            action_cell  = "[dim]OK[/dim]"
            shares_cell  = "—"
            diff_color   = "dim"
        elif diff_pct > 0:                 # overweight → trim
            action_cell  = "[yellow]TRIM[/yellow]"
            shares_cell  = f"[yellow]-{shares_delta}[/yellow]"
            diff_color   = "yellow"
        else:                              # underweight → add
            action_cell  = "[green]ADD[/green]"
            shares_cell  = f"[green]+{shares_delta}[/green]"
            diff_color   = "green"
            if shares_delta > 0 and execute:
                buys.append((symbol, shares_delta, price))

        table.add_row(
            symbol,
            f"${mkt_val:,.2f}",
            f"[{diff_color}]{actual_pct:.1%}[/{diff_color}]",
            f"{target_pct:.1%}",
            f"[{diff_color}]{diff_pct:+.1%}[/{diff_color}]",
            action_cell,
            shares_cell,
        )

    console.print(Panel(
        f"Positions: [bold]{n}[/bold]  |  "
        f"Position value: [bold]${position_value:,.2f}[/bold]  |  "
        f"Cash: [bold]${cash:,.2f}[/bold]  |  "
        f"Target per position: [bold]${target_value:,.2f}[/bold] ({target_pct:.1%})",
        title="Portfolio Summary", border_style="cyan",
    ))
    console.print(table)

    if execute and buys:
        console.print(f"\n[bold]Placing {len(buys)} buy order(s)...[/bold]")
        for symbol, shares, price in buys:
            if click.confirm(f"  BUY {shares}x {symbol} @ ~${price:.2f} (Paper)?", default=False):
                try:
                    order = MarketOrderRequest(
                        symbol=symbol, qty=shares,
                        side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
                    )
                    result = trading_client.submit_order(order)
                    console.print(f"  [green]Order submitted: {result.id}[/green]")
                except Exception as e:
                    console.print(f"  [red]Order error: {e}[/red]")
    elif execute:
        console.print("[dim]No ADD trades needed — portfolio is balanced.[/dim]")
    else:
        console.print("[dim]Dry run. Use --execute to place paper buy orders for ADD signals.[/dim]")


@cli.group()
def config():
    """View or edit ~/.buffet-bot-config.toml."""
    pass

@config.command('show')
def config_show():
    """Show effective config (file values merged with defaults): buffet-bot config show"""
    file_exists = os.path.exists(CONFIG_PATH)
    source = f"[dim](from {CONFIG_PATH})[/dim]" if file_exists else "[dim](defaults only — no config file)[/dim]"
    cfg = _load_config()

    table = Table(title=f"Effective Config  {source}", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Section", style="bold", no_wrap=True)
    table.add_column("Key", style="bold")
    table.add_column("Value")
    for section, values in cfg.items():
        for key, val in values.items():
            table.add_row(section, key, str(val))
    console.print(table)
    if not file_exists:
        console.print(f"[dim]Create a config file with: buffet-bot config init[/dim]")

@config.command('init')
@click.option('--force', is_flag=True, default=False, help='Overwrite existing config file.')
def config_init(force):
    """Write a default config file at ~/.buffet-bot-config.toml: buffet-bot config init"""
    if tomli_w is None:
        console.print("[red]tomli-w not installed. Run: pip install tomli-w[/red]")
        return
    if os.path.exists(CONFIG_PATH) and not force:
        console.print(f"[yellow]Config file already exists: {CONFIG_PATH}[/yellow]")
        console.print("[dim]Use --force to overwrite.[/dim]")
        return
    with open(CONFIG_PATH, 'wb') as f:
        tomli_w.dump(_CONFIG_DEFAULTS, f)
    console.print(f"[green]Config written: {CONFIG_PATH}[/green]")
    console.print("[dim]Edit it with any text editor, then re-run your commands.[/dim]")


@cli.group()
def alerts():
    """Set and check price/RSI threshold alerts."""
    pass

@alerts.command('set')
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--price-above', type=float, default=None, help='Trigger when price rises above this value.')
@click.option('--price-below', type=float, default=None, help='Trigger when price falls below this value.')
@click.option('--rsi-above',   type=float, default=None, help='Trigger when RSI rises above this value.')
@click.option('--rsi-below',   type=float, default=None, help='Trigger when RSI falls below this value.')
@click.option('--note', default='', help='Optional label for this alert.')
def alerts_set(ticker, price_above, price_below, rsi_above, rsi_below, note):
    """Set a price or RSI alert: buffet-bot alerts set AAPL --price-above 200"""
    ticker = ticker.upper()
    conditions = {
        'price_above': price_above,
        'price_below': price_below,
        'rsi_above':   rsi_above,
        'rsi_below':   rsi_below,
    }
    created = [(t, v) for t, v in conditions.items() if v is not None]
    if not created:
        console.print("[red]Specify at least one condition: --price-above, --price-below, --rsi-above, --rsi-below[/red]")
        return
    for alert_type, threshold in created:
        row_id = create_alert(ticker, alert_type, threshold, note)
        label = alert_type.replace('_', ' ')
        console.print(f"[green]Alert #{row_id} set:[/green] {ticker} {label} {threshold}"
                      + (f"  [dim]{note}[/dim]" if note else ""))

@alerts.command('list')
def alerts_list():
    """Show all active alerts: buffet-bot alerts list"""
    items = get_alerts(triggered=False)
    if not items:
        console.print("[yellow]No active alerts. Set one with: buffet-bot alerts set AAPL --price-above 200[/yellow]")
        return
    table = Table(title="Active Alerts", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("ID",        justify="right", style="dim")
    table.add_column("Ticker",    style="bold", no_wrap=True)
    table.add_column("Condition")
    table.add_column("Threshold", justify="right")
    table.add_column("Note",      style="dim")
    table.add_column("Set",       style="dim")
    for a in items:
        table.add_row(
            str(a['id']),
            a['ticker'],
            a['type'].replace('_', ' '),
            str(a['threshold']),
            a['note'],
            a['created_at'][:10],
        )
    console.print(table)

@alerts.command('remove')
@click.argument('alert_id', type=int)
def alerts_remove(alert_id):
    """Remove an alert by ID: buffet-bot alerts remove 3"""
    delete_alert(alert_id)
    console.print(f"[yellow]Alert #{alert_id} removed.[/yellow]")

@alerts.command('check')
def alerts_check():
    """Check all active alerts against current market data: buffet-bot alerts check"""
    items = get_alerts(triggered=False)
    if not items:
        console.print("[yellow]No active alerts to check.[/yellow]")
        return

    # Gather unique tickers and which data types we need per ticker
    tickers_needing_rsi = {a['ticker'] for a in items if 'rsi' in a['type']}
    unique_tickers = {a['ticker'] for a in items}

    prices = {}
    rsi_values = {}
    with console.status("[bold blue]Fetching market data...[/bold blue]"):
        for ticker in unique_tickers:
            live = get_realtime_data(ticker)
            prices[ticker] = live.get('price')
            if ticker in tickers_needing_rsi:
                tech = get_tech_indicators(ticker)
                rsi_values[ticker] = tech.get('rsi')

    triggered_ids = []
    table = Table(title="Alert Check", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("ID",        justify="right", style="dim")
    table.add_column("Ticker",    style="bold", no_wrap=True)
    table.add_column("Condition")
    table.add_column("Threshold", justify="right")
    table.add_column("Current",   justify="right")
    table.add_column("Status")

    for a in items:
        ticker    = a['ticker']
        threshold = a['threshold']
        atype     = a['type']

        if 'price' in atype:
            current = prices.get(ticker)
            label   = f"${current:.2f}" if current is not None else "—"
        else:
            current = rsi_values.get(ticker)
            label   = f"{current:.1f}" if current is not None else "—"

        fired = False
        if current is not None:
            if atype == 'price_above' and current > threshold:
                fired = True
            elif atype == 'price_below' and current < threshold:
                fired = True
            elif atype == 'rsi_above' and current > threshold:
                fired = True
            elif atype == 'rsi_below' and current < threshold:
                fired = True

        if fired:
            triggered_ids.append(a['id'])
            status_cell = "[bold green]TRIGGERED[/bold green]"
        else:
            status_cell = "[dim]waiting[/dim]"

        table.add_row(
            str(a['id']),
            ticker,
            atype.replace('_', ' '),
            str(threshold),
            label,
            status_cell,
        )

    console.print(table)

    if triggered_ids:
        console.print(f"\n[bold green]{len(triggered_ids)} alert(s) triggered.[/bold green]")
        for aid in triggered_ids:
            mark_alert_triggered(aid)
        console.print("[dim]Triggered alerts removed from active list.[/dim]")
    else:
        console.print("[dim]No alerts triggered.[/dim]")


@cli.group()
def watchlist():
    """Manage your personal stock watchlist."""
    pass

@watchlist.command('add')
@click.argument('ticker', shell_complete=_complete_ticker)
def watchlist_add(ticker):
    """Add a ticker to your watchlist: buffet-bot watchlist add TSLA"""
    ticker = ticker.upper()
    add_to_watchlist(ticker)
    console.print(f"[green]Added [bold]{ticker}[/bold] to watchlist.[/green]")

@watchlist.command('remove')
@click.argument('ticker', shell_complete=_complete_ticker)
def watchlist_remove(ticker):
    """Remove a ticker from your watchlist: buffet-bot watchlist remove TSLA"""
    ticker = ticker.upper()
    remove_from_watchlist(ticker)
    console.print(f"[yellow]Removed [bold]{ticker}[/bold] from watchlist.[/yellow]")

@watchlist.command('show')
def watchlist_show():
    """Show all tickers in your watchlist: buffet-bot watchlist show"""
    items = get_watchlist()
    if not items:
        console.print("[yellow]Your watchlist is empty. Add tickers with: buffet-bot watchlist add TSLA[/yellow]")
        return
    table = Table(title="My Watchlist", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Ticker", style="bold", no_wrap=True)
    table.add_column("Added", style="dim")
    for item in items:
        table.add_row(item['ticker'], item['added_at'])
    console.print(table)
    console.print(f"[dim]{len(items)} ticker(s). Scan with: buffet-bot scan --watchlist[/dim]")


@cli.command()
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--expiry', default=None,
              help='Expiration date (YYYY-MM-DD). Defaults to nearest available.')
@click.option('--top', default=5, show_default=True, type=int,
              help='Number of top-volume strikes to show per side.')
def options(ticker, expiry, top):
    """Options chain: put/call ratio, unusual volume, top strikes: buffet-bot options AAPL"""
    ticker = ticker.upper()
    try:
        t = yf.Ticker(ticker)
        expirations = t.options
    except Exception as e:
        console.print(f"[red]Could not fetch options for {ticker}: {e}[/red]")
        return

    if not expirations:
        console.print(f"[yellow]No options data available for {ticker}.[/yellow]")
        return

    if expiry:
        if expiry not in expirations:
            console.print(f"[red]Expiry {expiry} not found. Available: {', '.join(expirations[:5])}...[/red]")
            return
        selected = expiry
    else:
        selected = expirations[0]

    try:
        chain = t.option_chain(selected)
        calls = chain.calls.copy()
        puts  = chain.puts.copy()
    except Exception as e:
        console.print(f"[red]Could not fetch chain for {selected}: {e}[/red]")
        return

    # Compute summary stats
    call_vol  = int(calls['volume'].fillna(0).sum())
    put_vol   = int(puts['volume'].fillna(0).sum())
    pc_ratio  = put_vol / call_vol if call_vol > 0 else float('inf')
    pc_color  = 'red' if pc_ratio > 1.2 else ('green' if pc_ratio < 0.8 else 'yellow')

    # ATM IV — strike closest to current price
    live = get_realtime_data(ticker)
    spot = live.get('price', 0)
    atm_iv = None
    if spot and not calls.empty:
        closest_idx = (calls['strike'] - spot).abs().idxmin()
        atm_iv = calls.loc[closest_idx, 'impliedVolatility']

    console.print(Panel(
        f"Expiry: [bold]{selected}[/bold]  |  Spot: [bold]${spot:.2f}[/bold]  |  "
        f"Call vol: [green]{call_vol:,}[/green]  |  "
        f"Put vol: [red]{put_vol:,}[/red]  |  "
        f"P/C ratio: [{pc_color}]{pc_ratio:.2f}[/{pc_color}]"
        + (f"  |  ATM IV: [yellow]{atm_iv:.1%}[/yellow]" if atm_iv else ""),
        title=f"[bold]{ticker}[/bold] Options Chain",
        border_style="cyan",
    ))

    # Flag unusual volume — > 2× average volume for each side
    def _unusual(df):
        avg = df['volume'].fillna(0).mean()
        return df['volume'].fillna(0) > max(avg * 2, 100)

    calls['unusual'] = _unusual(calls)
    puts['unusual']  = _unusual(puts)

    def _chain_table(df, side, color):
        top_df = df.nlargest(top, 'volume').copy()
        tbl = Table(title=f"Top {top} {side} by Volume", box=box.ROUNDED,
                    header_style=f"bold {color}")
        tbl.add_column("Strike",  justify="right")
        tbl.add_column("Last",    justify="right")
        tbl.add_column("Bid/Ask", justify="right")
        tbl.add_column("Volume",  justify="right")
        tbl.add_column("OI",      justify="right")
        tbl.add_column("IV",      justify="right")
        tbl.add_column("ITM",     justify="center")
        tbl.add_column("Flag",    justify="center")
        for _, row in top_df.iterrows():
            vol   = int(row.get('volume', 0) or 0)
            oi    = int(row.get('openInterest', 0) or 0)
            iv    = row.get('impliedVolatility', 0) or 0
            itm   = '[bold green]ITM[/bold green]' if row.get('inTheMoney') else ''
            flag  = '[bold yellow]UNUSUAL[/bold yellow]' if row.get('unusual') else ''
            bid   = row.get('bid', 0) or 0
            ask   = row.get('ask', 0) or 0
            tbl.add_row(
                f"${row['strike']:.2f}",
                f"${row.get('lastPrice', 0):.2f}",
                f"${bid:.2f}/${ask:.2f}",
                f"[{color}]{vol:,}[/{color}]",
                f"{oi:,}",
                f"{iv:.1%}",
                itm,
                flag,
            )
        return tbl

    console.print(_chain_table(calls, "Calls", "green"))
    console.print(_chain_table(puts,  "Puts",  "red"))

    pc_interp = (
        "Bearish sentiment (more puts than calls)" if pc_ratio > 1.2
        else "Bullish sentiment (more calls than puts)" if pc_ratio < 0.8
        else "Neutral sentiment"
    )
    console.print(f"[dim]{pc_interp}  |  {len(expirations)} expiries available[/dim]")


@cli.command()
@click.option('--confidence', default=0.95, show_default=True, type=float,
              help='Confidence level for VaR (e.g. 0.95 = 95%, 0.99 = 99%).')
@click.option('--days', default=252, show_default=True, type=int,
              help='Lookback window in trading days for historical simulation.')
def var(confidence, days):
    """Portfolio Value-at-Risk (historical simulation): buffet-bot var

    \b
    Computes 1-day VaR and CVaR for your current Alpaca paper positions
    using historical daily returns over the last --days trading days.

    Examples:
      buffet-bot var                  # 95% VaR, 1-year history
      buffet-bot var --confidence 0.99 --days 504
    """
    try:
        positions = trading_client.get_all_positions()
    except Exception as e:
        console.print(f"[red]Could not fetch positions: {e}[/red]")
        return

    if not positions:
        console.print("[yellow]No open positions. Open some trades first.[/yellow]")
        return

    pct_str = f"{int(confidence * 100)}%"
    with console.status(f"[bold blue]Fetching {days}d price history for VaR...[/bold blue]"):
        result = _calculate_portfolio_var(positions, confidence=confidence, lookback_days=days)

    if result is None:
        console.print("[yellow]Insufficient price history to calculate VaR "
                      "(need at least 30 trading days).[/yellow]")
        return

    tail_pct = 100 - int(confidence * 100)
    console.print(Panel(
        f"Portfolio value:     [bold]${result['portfolio_val']:,.2f}[/bold]\n"
        f"Positions:           [dim]{', '.join(result['tickers'])}[/dim]\n"
        f"History used:        {result['n_days']} trading days\n\n"
        f"{pct_str} 1-Day VaR:   [bold red]-${result['var_1d']:,.2f}[/bold red]  "
        f"([bold red]{result['var_pct']:.2f}%[/bold red] of portfolio)\n"
        f"{pct_str} CVaR (ES):   [bold red]-${result['cvar_1d']:,.2f}[/bold red]  "
        f"[dim](expected loss beyond VaR)[/dim]\n\n"
        f"[dim]On the worst {tail_pct}% of trading days, this portfolio is "
        f"expected to lose more than ${result['var_1d']:,.2f}.[/dim]",
        title=f"[bold]Portfolio Value at Risk — {pct_str} / 1-Day[/bold]",
        border_style="red",
    ))


@cli.command()
@click.argument('shell', type=click.Choice(['bash', 'zsh', 'fish']), default='bash')
def completion(shell):
    """Print shell tab-completion setup: buffet-bot completion bash|zsh|fish"""
    prog = 'buffet-bot'
    env_var = '_BUFFET_BOT_COMPLETE'
    if shell == 'bash':
        line = f'eval "$({env_var}=bash_source {prog})"'
        dest = '~/.bashrc'
    elif shell == 'zsh':
        line = f'eval "$({env_var}=zsh_source {prog})"'
        dest = '~/.zshrc'
    else:
        line = f'{env_var}=fish_source {prog} | source'
        dest = '~/.config/fish/completions/buffet-bot.fish'
    console.print(Panel(
        f"Add this line to [cyan]{dest}[/cyan] then restart your shell:\n\n"
        f"  [bold green]{line}[/bold green]\n\n"
        f"[dim]Completes commands, --risk/--model/--strategy options, and TICKER "
        f"arguments (your watchlist + {len(_COMMON_TICKERS)} common stocks).[/dim]",
        title="[bold]Shell Completion Setup[/bold]",
        border_style="cyan",
    ))


@cli.group()
def beats():
    """Log and review earnings beat/miss history."""
    pass


@beats.command('log')
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--eps-actual',   required=True, type=float, help='Reported EPS.')
@click.option('--eps-forecast', required=True, type=float, help='Analyst consensus EPS forecast.')
@click.option('--date', 'report_date', default=None,
              help='Report date YYYY-MM-DD (defaults to today).')
def beats_log(ticker, eps_actual, eps_forecast, report_date):
    """Record an earnings result against the analyst forecast.

    \b
    Examples:
      buffet-bot beats log AAPL --eps-actual 2.18 --eps-forecast 2.10
      buffet-bot beats log MSFT --eps-actual 3.05 --eps-forecast 3.20 --date 2026-01-29
    """
    ticker = ticker.upper()
    if report_date is None:
        report_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    inserted = log_earnings_result(ticker, report_date, eps_actual, eps_forecast)
    if not inserted:
        console.print(f"[yellow]Duplicate entry: {ticker} on {report_date} already logged.[/yellow]")
        return
    surprise_pct = ((eps_actual - eps_forecast) / abs(eps_forecast) * 100
                    if eps_forecast != 0 else 0.0)
    sign   = '+' if surprise_pct >= 0 else ''
    color  = 'green' if surprise_pct >= 3 else ('red' if surprise_pct <= -3 else 'yellow')
    label  = 'BEAT' if surprise_pct >= 3 else ('MISS' if surprise_pct <= -3 else 'IN-LINE')
    console.print(Panel(
        f"Ticker:    [bold cyan]{ticker}[/bold cyan]\n"
        f"Date:      {report_date}\n"
        f"Actual:    ${eps_actual:.4f}\n"
        f"Forecast:  ${eps_forecast:.4f}\n"
        f"Surprise:  [{color}][bold]{sign}{surprise_pct:.1f}%  {label}[/bold][/{color}]",
        title="[bold]Earnings Result Logged[/bold]",
        border_style=color,
    ))


@beats.command('show')
@click.argument('ticker', default='', required=False, shell_complete=_complete_ticker)
@click.option('--limit', default=20, show_default=True, type=int,
              help='Max rows to show.')
def beats_show(ticker, limit):
    """Display earnings beat/miss history.

    \b
    Examples:
      buffet-bot beats show          # all tickers, most recent first
      buffet-bot beats show AAPL     # just Apple
      buffet-bot beats show AAPL --limit 8
    """
    rows = get_earnings_history(ticker, limit)
    if not rows:
        msg = f"No earnings history for [bold]{ticker.upper()}[/bold]." if ticker \
              else "No earnings history logged yet. Use: buffet-bot beats log TICKER --eps-actual X --eps-forecast Y"
        console.print(f"[yellow]{msg}[/yellow]")
        return

    tbl = Table(
        title=f"Earnings Surprise History — {ticker.upper() or 'All Tickers'}",
        box=box.ROUNDED, header_style="bold blue",
    )
    tbl.add_column("Ticker",   style="bold cyan",  no_wrap=True)
    tbl.add_column("Date",     style="dim",         no_wrap=True)
    tbl.add_column("Actual",   justify="right")
    tbl.add_column("Forecast", justify="right")
    tbl.add_column("Surprise", justify="right")
    tbl.add_column("Result",   justify="center")

    beat_count = miss_count = inline_count = 0
    for r in rows:
        bm = r['beat_miss']
        color = 'green' if bm == 'BEAT' else ('red' if bm == 'MISS' else 'yellow')
        sign  = '+' if r['surprise_pct'] >= 0 else ''
        tbl.add_row(
            r['ticker'],
            r['report_date'],
            f"${r['eps_actual']:.4f}",
            f"${r['eps_forecast']:.4f}",
            f"[{color}]{sign}{r['surprise_pct']:.1f}%[/{color}]",
            f"[{color}][bold]{bm}[/bold][/{color}]",
        )
        if bm == 'BEAT':    beat_count  += 1
        elif bm == 'MISS':  miss_count  += 1
        else:               inline_count += 1

    console.print(tbl)
    total = beat_count + miss_count + inline_count
    beat_rate = beat_count / total * 100 if total else 0
    console.print(
        f"  [green]Beats: {beat_count}[/green]  "
        f"[red]Misses: {miss_count}[/red]  "
        f"[yellow]In-line: {inline_count}[/yellow]  "
        f"[dim]Beat rate: {beat_rate:.0f}%[/dim]"
    )


def main():
    cli()

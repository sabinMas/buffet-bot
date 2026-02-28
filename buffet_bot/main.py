import os
import json
import click
import requests
from datetime import datetime, timezone, timedelta
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

PLANS_DIR = os.path.expanduser("~/.buffet-plans")
DB_PATH = os.path.expanduser("~/.buffet-bot.db")

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
        })
        return metrics
    except Exception as e:
        console.print(f"[red]Metrics error for {ticker}: {e}[/red]")
        return {'score': 0}

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

# ── LLM helpers ──────────────────────────────────────────────────────────────

def _query_llms_freeform(prompt_text, primary_model):
    """Query both LLMs with a plain-text prompt, returning raw text responses."""
    models_to_query = [primary_model]
    if primary_model != MODELS[1]:
        models_to_query.append(MODELS[1])

    responses = {}
    for model in models_to_query:
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
    hist = yf.download(ticker, period='6mo', progress=False)['Close'].tail(30)
    buffett = get_buffett_metrics(ticker)
    tech = get_tech_indicators(ticker) if risk == 'high' else {}
    realtime = get_realtime_data(ticker)
    news = get_recent_news(ticker)
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

    strategy_guidance = STRATEGY_PROMPTS.get(strategy, STRATEGY_PROMPTS['value'])

    prompt = f"""
    Buffett Trading AI for {ticker} | Risk: {risk} | Strategy: {strategy.upper()}
    Buffett Score: {buffett['score']}/100 | ROE: {buffett.get('roe','?')}% | ROIC: {buffett.get('roic','?')}% | Debt/Eq: {buffett.get('debt_eq','?')} | OpMargin: {buffett.get('op_margin','?')}% | FCF Yield: {buffett.get('fcf_yield','?')}% | P/E: {buffett.get('pe','?')} | P/B: {buffett.get('pb','?')}
    {live_block}{news_block}
    Recent Prices (30d): {hist.to_dict()}
    Tech {'(RSI: ' + str(tech.get('rsi', 'N/A')) + ', MACD: ' + str(tech.get('macd', 'N/A')) + ')' if tech else ''}

    Strategy Guidance: {strategy_guidance}

    Additional rules:
    - Risk mgmt: Position <2% portfolio, stop-loss 3-7%
    - Factor in the LIVE price and news sentiment score above

    JSON only: {{"action": "BUY|SELL|HOLD", "confidence": 0.85, "qty": 10, "reason": "2 sentences", "stop_pct": 0.05}}
    """

    models_to_query = [primary_model]
    if primary_model != MODELS[1]:
        models_to_query.append(MODELS[1])

    responses = {}
    for model in models_to_query:
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
    plan_data['name'] = name
    plan_data['updated_at'] = datetime.now().isoformat()
    path = os.path.join(PLANS_DIR, f"{name}.json")
    with open(path, 'w') as f:
        json.dump(plan_data, f, indent=2, default=str)
    return path

def _load_plan(name):
    path = os.path.join(PLANS_DIR, f"{name}.json")
    if not os.path.exists(path):
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
        s_color = "green" if score >= 70 else "yellow" if score >= 40 else "red"

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
        path = _save_plan(plan_name, plan_data)
        console.print(f"[green]Plan saved: {path}[/green]")
        console.print(f"[dim]Re-run any time: buffet-bot guide --plan {plan_name}[/dim]")

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


def _calculate_position_size(ticker, confidence, cash, risk_pct=0.02):
    """ATR-based dynamic position sizing."""
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
        qty         = max(1, math.floor(dollar_size / price))
        return {
            'qty':         qty,
            'dollar_size': round(dollar_size, 2),
            'atr_pct':     round(atr_pct * 100, 2),
            'atr':         round(atr, 4),
            'price':       price,
        }
    except Exception:
        return None


def _check_sell_signals(pos_list):
    """Check each position for sell signals.
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

        if entry and current < entry * 0.93:
            signals.append('STOP')

        b_score = get_buffett_metrics(pos.symbol).get('score', 0)
        if b_score < 40:
            signals.append('THESIS_BROKEN')

        if pnl_threshold is not None and pnl_pcts[i] <= pnl_threshold:
            signals.append('UNDERPERFORM')

        rsi_val = None
        try:
            tech    = get_tech_indicators(pos.symbol)
            rsi_val = tech.get('rsi')
            if rsi_val and rsi_val > 72:
                signals.append('OVERBOUGHT')
        except Exception:
            pass

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
@click.argument('ticker')
@click.option('--risk', type=click.Choice(['low', 'medium', 'high']), default='medium')
@click.option('--dry-run/--execute', default=True)
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
@click.option('--strategy', type=click.Choice(['value', 'growth', 'dividend', 'turnaround']),
              default='value', show_default=True, help='Investment strategy lens.')
def analyze(ticker, risk, dry_run, primary_model, strategy):
    """Analyze stock: buffet-bot analyze AAPL --risk high --strategy growth"""
    console.print(Panel(
        f"[bold]{ticker}[/bold]  |  Risk: [yellow]{risk}[/yellow]  |  Strategy: [cyan]{strategy}[/cyan]",
        title="Analyzing", border_style="blue"))

    result = _run_analysis(ticker, risk, primary_model, strategy)
    _print_live_market(ticker, result['realtime'], result['news'])
    _print_ai_responses(result['responses'])
    console.print(f"\nConsensus: {_consensus_text(result['consensus'])}")

    sizing = None
    if result['consensus'] == 'BUY':
        try:
            account    = trading_client.get_account()
            cash       = float(account.cash)
            confidence = result['best_buy_resp'].get('confidence', 0.5) if result['best_buy_resp'] else 0.5
            sizing     = _calculate_position_size(ticker, confidence, cash)
            if sizing:
                llm_qty = result['best_buy_resp'].get('qty', 1) if result['best_buy_resp'] else 1
                console.print(Panel(
                    f"Account cash: [bold]${cash:,.2f}[/bold]\n"
                    f"LLM suggested qty:   [dim]{llm_qty}[/dim]\n"
                    f"Formula qty:         [bold cyan]{sizing['qty']}[/bold cyan]  "
                    f"(${sizing['dollar_size']:,.2f})\n"
                    f"ATR: ${sizing['atr']:.2f}  ({sizing['atr_pct']}% of price)",
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
@click.argument('ticker')
@click.option('--risk', type=click.Choice(['low', 'medium', 'high']), default='medium')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
@click.option('--strategy', type=click.Choice(['value', 'growth', 'dividend', 'turnaround']),
              default='value', show_default=True, help='Investment strategy lens.')
def buy(ticker, risk, primary_model, strategy):
    """Analyze then immediately prompt to buy: buffet-bot buy AAPL --strategy dividend"""
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
        sizing     = _calculate_position_size(ticker, confidence, cash)
        if sizing:
            llm_qty = result['best_buy_resp'].get('qty', 1)
            console.print(Panel(
                f"Account cash: [bold]${cash:,.2f}[/bold]\n"
                f"LLM suggested qty:   [dim]{llm_qty}[/dim]\n"
                f"Formula qty:         [bold cyan]{sizing['qty']}[/bold cyan]  "
                f"(${sizing['dollar_size']:,.2f})\n"
                f"ATR: ${sizing['atr']:.2f}  ({sizing['atr_pct']}% of price)",
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
def scan():
    """Scan top stocks for Buffett opportunities"""
    tickers = ['AAPL', 'MSFT', 'GOOGL', 'BRK-B', 'JNJ', 'V', 'JPM', 'PG']
    scores = {}
    with console.status("[bold blue]Scanning watchlist...[/bold blue]"):
        for t in tickers:
            scores[t] = get_buffett_metrics(t)['score']
            time.sleep(1)

    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]

    table = Table(title="Top Buffett Scores", box=box.ROUNDED, header_style="bold blue")
    table.add_column("Ticker", style="bold cyan")
    table.add_column("Buffett Score", justify="right")

    for ticker, score in top:
        color = "green" if score >= 70 else "yellow" if score >= 40 else "red"
        table.add_row(ticker, f"[{color}]{score}[/{color}]")

    console.print(table)

@cli.command()
def status():
    """Check account status"""
    account = trading_client.get_account()
    console.print(Panel(
        f"Cash:          [bold green]${float(account.cash):,.2f}[/bold green]\n"
        f"Buying Power:  [bold cyan]${float(account.buying_power):,.2f}[/bold cyan]",
        title="Paper Account",
        border_style="blue",
    ))

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
        plan_data = _load_plan(plan)
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
@click.option('--run', 'run_plan', default=None, help='Execute a saved plan.')
@click.option('--delete', 'delete_plan', default=None, help='Delete a saved plan.')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def plans(run_plan, delete_plan, primary_model):
    """List, run, or delete saved investment plans.

    \b
    Examples:
      buffet-bot plans                    # list all saved plans
      buffet-bot plans --run my-plan      # re-analyze and execute a plan
      buffet-bot plans --delete my-plan   # remove a plan
    """
    if delete_plan:
        path = os.path.join(PLANS_DIR, f"{delete_plan}.json")
        if os.path.exists(path):
            os.remove(path)
            console.print(f"[green]Deleted plan '{delete_plan}'.[/green]")
        else:
            console.print(f"[red]Plan '{delete_plan}' not found.[/red]")
        return

    if run_plan:
        plan_data = _load_plan(run_plan)
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
    table.add_column("Name", style="bold cyan")
    table.add_column("Goal")
    table.add_column("Tickers")
    table.add_column("Budget", justify="right")
    table.add_column("Risk")
    table.add_column("Last Updated", style="dim")
    for p in saved:
        table.add_row(
            p.get('name', '?'),
            p.get('goal', 'custom'),
            ', '.join(p.get('tickers', [])),
            f"${p.get('budget', 0):,.2f}",
            p.get('risk', 'medium'),
            (p.get('updated_at') or p.get('created_at', ''))[:10],
        )
    console.print(table)
    console.print("\n[dim]Run a plan:    buffet-bot plans --run <name>[/dim]")
    console.print("[dim]Delete a plan: buffet-bot plans --delete <name>[/dim]")


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
@click.argument('ticker')
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
def check_sells(execute):
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
        results = _check_sell_signals(positions)

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
        s_color   = "green" if b_score >= 70 else "yellow" if b_score >= 40 else "red"
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
@click.argument('ticker')
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
@click.argument('ticker')
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
def dashboard(tickers):
    """Live multi-ticker price dashboard: buffet-bot dashboard AAPL MSFT GOOGL TSLA"""
    if not tickers:
        tickers = ('AAPL', 'MSFT', 'GOOGL', 'TSLA')
    tickers = tuple(t.upper() for t in tickers)

    console.print(Panel(
        f"Watching: [bold]{', '.join(tickers)}[/bold]\n"
        "[dim]Press Ctrl+C to exit[/dim]",
        border_style="blue"))

    try:
        while True:
            rows = {t: get_realtime_data(t) for t in tickers}
            console.clear()

            tbl = Table(title="Live Dashboard", box=box.ROUNDED, header_style="bold blue")
            tbl.add_column("Ticker",  style="bold cyan")
            tbl.add_column("Price",   justify="right")
            tbl.add_column("Change%", justify="right")
            tbl.add_column("High",    justify="right")
            tbl.add_column("Low",     justify="right")
            tbl.add_column("Volume",  justify="right")
            tbl.add_column("Updated", style="dim")

            now_str = datetime.now().strftime('%H:%M:%S')
            for t in tickers:
                d     = rows.get(t) or {}
                chg   = d.get('change_pct', 0)
                color = "green" if chg >= 0 else "red"
                sign  = '+' if chg >= 0 else ''
                tbl.add_row(
                    t,
                    f"${d['price']:.2f}"        if 'price'      in d else "—",
                    f"[{color}]{sign}{chg:.2f}%[/{color}]" if 'change_pct' in d else "—",
                    f"${d['high']:.2f}"          if 'high'       in d else "—",
                    f"${d['low']:.2f}"           if 'low'        in d else "—",
                    f"{d['volume']:,}"           if 'volume'     in d else "—",
                    now_str,
                )

            console.print(tbl)
            console.print(Panel(
                "[dim]Refreshing every 60s — Ctrl+C to exit[/dim]",
                border_style="dim"))
            time.sleep(60)
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard stopped.[/dim]")


def main():
    cli()

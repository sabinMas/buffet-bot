import os
import json
import click
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
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
console = Console()

MODELS = ['deepseek-r1', 'qwen2.5:7b']
ALPACA_PAPER_BASE = 'https://paper-api.alpaca.markets'

MODEL_COLORS = {
    'deepseek-r1': 'cyan',
    'qwen2.5:7b': 'magenta',
}

@click.group()
@click.version_option()
def cli():
    """Buffett AI Trading Bot CLI - Local LLM powered"""
    pass

# ── Data helpers ─────────────────────────────────────────────────────────────

def get_buffett_metrics(ticker):
    """Calculate Buffett value score: ROE, Debt/Equity, Margins"""
    try:
        info = yf.Ticker(ticker).info
        roe = float(info.get('returnOnEquity', 0)) * 100
        debt_eq = float(info.get('debtToEquity', 999))
        op_margin = float(info.get('operatingMargins', 0)) * 100
        score = 0
        metrics = {}
        if roe > 15:
            score += 40
            metrics['roe_pass'] = True
        if debt_eq < 50:
            score += 30
            metrics['debt_pass'] = True
        if op_margin > 10:
            score += 30
            metrics['margin_pass'] = True
        metrics['score'] = score
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

def _run_analysis(ticker, risk, primary_model):
    """Fetch data, query LLMs, compute consensus. Returns analysis dict."""
    hist = yf.download(ticker, period='6mo', progress=False)['Close'].tail(30)
    buffett = get_buffett_metrics(ticker)
    tech = get_tech_indicators(ticker) if risk == 'high' else {}

    prompt = f"""
    Buffett Trading AI for {ticker} | Risk: {risk}
    Buffett Score: {buffett['score']}/100 ({buffett})
    Recent Prices (30d): {hist.to_dict()}
    Tech {'(RSI: ' + str(tech.get('rsi', 'N/A')) + ', MACD: ' + str(tech.get('macd', 'N/A')) + ')' if tech else ''}

    Analyze using:
    - Warren Buffett: Value (high ROE/moat), consistent earnings
    - Math predictions: Regression, momentum
    - Risk mgmt: Position <2% portfolio, stop-loss 3-7%

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

    return {
        'buffett': buffett,
        'tech': tech,
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
def analyze(ticker, risk, dry_run, primary_model):
    """Analyze stock: buffet-bot analyze AAPL --risk high"""
    console.print(Panel(f"[bold]{ticker}[/bold]  |  Risk: [yellow]{risk}[/yellow]",
                        title="Analyzing", border_style="blue"))

    result = _run_analysis(ticker, risk, primary_model)
    _print_ai_responses(result['responses'])
    console.print(f"\nConsensus: {_consensus_text(result['consensus'])}")

    if not dry_run and result['consensus'] == 'BUY':
        if click.confirm(f'Execute BUY {ticker}? (Paper)'):
            if result['best_buy_resp']:
                _place_order(ticker, result['best_buy_resp'])
            else:
                console.print("[yellow]No valid BUY signal[/yellow]")

@cli.command()
@click.argument('ticker')
@click.option('--risk', type=click.Choice(['low', 'medium', 'high']), default='medium')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def buy(ticker, risk, primary_model):
    """Analyze then immediately prompt to buy: buffet-bot buy AAPL"""
    console.print(Panel(f"[bold]{ticker}[/bold]  |  Risk: [yellow]{risk}[/yellow]",
                        title="Analyzing", border_style="blue"))

    result = _run_analysis(ticker, risk, primary_model)
    _print_ai_responses(result['responses'])
    console.print(f"\nConsensus: {_consensus_text(result['consensus'])}")

    if result['consensus'] != 'BUY':
        console.print(f"[yellow]Consensus is {result['consensus']} — no order placed.[/yellow]")
        return

    if not result['best_buy_resp']:
        console.print("[yellow]No valid BUY signal from models — no order placed.[/yellow]")
        return

    if click.confirm(f'Execute BUY {ticker}? (Paper)'):
        _place_order(ticker, result['best_buy_resp'])

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

def main():
    cli()

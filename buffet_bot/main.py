import os
import json
import click
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

load_dotenv()

# Initialize Alpaca paper trading client
API_KEY = os.getenv('ALPACA_API_KEY')
SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
if not API_KEY or not SECRET_KEY:
    raise ValueError("Add ALPACA_API_KEY and ALPACA_SECRET_KEY to .env")

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)

MODELS = ['deepseek-r1', 'qwen2.5:7b']

@click.group()
@click.version_option()
def cli():
    """Buffett AI Trading Bot CLI - Local LLM powered"""
    pass

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
        click.echo(f"Metrics error for {ticker}: {e}")
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
                options={'temperature': 0.5}
            )
            responses[model] = resp['message']['content'].strip()
        except Exception as e:
            responses[model] = f"Error: {e}"
    return responses

def _run_analysis(ticker, risk, primary_model):
    """Fetch data, query LLMs, and compute consensus. Returns analysis dict."""
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
            default=None
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
            time_in_force=TimeInForce.DAY
        )
        result = trading_client.submit_order(order)
        click.echo(f"Order submitted: {result.id}")
    except Exception as e:
        click.echo(f"Order error: {e}")

@cli.command()
@click.argument('question')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def ask(question, primary_model):
    """Ask the AI a free-form investing question: buffet-bot ask "What is a P/E ratio?" """
    # Try to enrich the prompt with real ticker symbols related to the question
    ticker_context = ""
    try:
        results = yf.Search(question).quotes
        if results:
            symbols = [q.get('symbol', '') for q in results[:5] if q.get('symbol')]
            if symbols:
                ticker_context = f"\nRelated tickers from your question: {', '.join(symbols)}"
    except Exception:
        pass  # Ticker search failure never blocks the LLM call

    prompt = f"""You are a knowledgeable investing assistant guided by Warren Buffett's value investing principles.
Answer the following question thoughtfully and concisely, referencing relevant financial concepts where appropriate.{ticker_context}

Question: {question}"""

    click.echo(f"\nAsking: {question}\n")
    responses = _query_llms_freeform(prompt, primary_model)
    for model_name, response_text in responses.items():
        click.echo(f"--- {model_name} ---")
        click.echo(response_text)
        click.echo()

@cli.command()
@click.argument('query')
def lookup(query):
    """Look up a ticker by company name: buffet-bot lookup Apple"""
    try:
        results = yf.Search(query).quotes
    except Exception as e:
        click.echo(f"Search error: {e}")
        return

    if not results:
        click.echo(f"No results found for '{query}'.")
        return

    rows = []
    for q in results:
        rows.append({
            'Symbol': q.get('symbol', ''),
            'Company Name': q.get('longname') or q.get('shortname', ''),
            'Exchange': q.get('exchange', ''),
            'Type': q.get('quoteType', ''),
        })

    df = pd.DataFrame(rows)
    click.echo(df.to_string(index=False))
    click.echo("\nTip: Run 'buffet-bot analyze <SYMBOL>' to analyze any ticker above.")

@cli.command()
@click.argument('ticker')
@click.option('--risk', type=click.Choice(['low', 'medium', 'high']), default='medium')
@click.option('--dry-run/--execute', default=True)
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def analyze(ticker, risk, dry_run, primary_model):
    """Analyze stock: buffet-bot analyze AAPL --risk high"""
    click.echo(f"Analyzing {ticker} (Risk: {risk})")

    result = _run_analysis(ticker, risk, primary_model)

    click.echo("\nAI Responses:")
    click.echo(json.dumps(result['responses'], indent=2))
    click.echo(f"\nConsensus: {result['consensus']}")

    if not dry_run and result['consensus'] == 'BUY':
        if click.confirm(f'Execute BUY {ticker}? (Paper)'):
            if result['best_buy_resp']:
                _place_order(ticker, result['best_buy_resp'])
            else:
                click.echo("No valid BUY signal")

@cli.command()
@click.argument('ticker')
@click.option('--risk', type=click.Choice(['low', 'medium', 'high']), default='medium')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def buy(ticker, risk, primary_model):
    """Analyze then immediately prompt to buy: buffet-bot buy AAPL"""
    click.echo(f"Analyzing {ticker} (Risk: {risk})")

    result = _run_analysis(ticker, risk, primary_model)

    click.echo("\nAI Responses:")
    click.echo(json.dumps(result['responses'], indent=2))
    click.echo(f"\nConsensus: {result['consensus']}")

    if result['consensus'] != 'BUY':
        click.echo(f"Consensus is {result['consensus']} — no order placed.")
        return

    if not result['best_buy_resp']:
        click.echo("No valid BUY signal from models — no order placed.")
        return

    if click.confirm(f'Execute BUY {ticker}? (Paper)'):
        _place_order(ticker, result['best_buy_resp'])

@cli.command()
@click.option('--limit', default=20, show_default=True, help='Max number of orders to show.')
@click.option('--ticker', default=None, help='Filter by ticker symbol.')
@click.option('--status', 'order_status', default='all', type=click.Choice(['all', 'open', 'closed']), show_default=True)
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
        click.echo(f"Error fetching orders: {e}")
        return

    if not orders:
        click.echo("No orders found.")
        return

    rows = []
    for o in orders:
        rows.append({
            'Date': str(o.submitted_at)[:19] if o.submitted_at else '',
            'Symbol': o.symbol,
            'Side': o.side.value.upper(),
            'Qty': o.qty,
            'Fill Price': o.filled_avg_price or '',
            'Status': o.status.value,
        })

    df = pd.DataFrame(rows)
    click.echo(df.to_string(index=False))

@cli.command()
def scan():
    """Scan top stocks for Buffett opportunities"""
    tickers = ['AAPL', 'MSFT', 'GOOGL', 'BRK-B', 'JNJ', 'V', 'JPM', 'PG']
    scores = {}
    for t in tickers:
        scores[t] = get_buffett_metrics(t)['score']
        time.sleep(1)
    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
    df = pd.DataFrame(top, columns=['Ticker', 'Buffett Score'])
    click.echo(df.to_string(index=False))

@cli.command()
def status():
    """Check account status"""
    account = trading_client.get_account()
    click.echo(f"Paper Account: ${float(account.cash):.2f} cash, Buying Power ${float(account.buying_power):.2f}")

def main():
    cli()

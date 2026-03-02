"""Trading commands — ask, lookup, browse, analyze, buy, history, portfolio,
chat, scan, status, stream, chart, dashboard."""
import contextlib
import json
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import click
import ollama
import pandas as pd
import plotext as plt
import yfinance as yf
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich import box

from buffet_bot.globals import (
    API_KEY, SECRET_KEY, trading_client, console,
    MODELS, MODEL_COLORS, ALPACA_PAPER_BASE, _CONFIG,
)
from buffet_bot.db import get_watchlist
from buffet_bot.data import (
    get_buffett_metrics, get_realtime_data, get_recent_news,
    _get_earnings_date, _complete_ticker,
)
from buffet_bot.display import (
    _print_ai_responses, _consensus_text, _score_color,
    _change_color, _print_live_market, _make_panel_title,
)
from buffet_bot.analysis import _run_analysis, _place_order, _query_llms_freeform
from buffet_bot.risk import _calculate_position_size
from buffet_bot.crypto import (
    CRYPTO_SYMBOLS, is_crypto_symbol,
    get_crypto_quote, get_crypto_volatility,
    analyze_crypto as _analyze_crypto,
)

import requests


@click.command()
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

    console.print(Panel(question, title=_make_panel_title("Question", "bright_cyan"), border_style="bright_cyan", box=box.ROUNDED))
    responses = _query_llms_freeform(prompt, primary_model)
    for model_name, response_text in responses.items():
        color = MODEL_COLORS.get(model_name, 'bright_cyan')
        if color == 'cyan':
            color = 'bright_cyan'
        elif color == 'magenta':
            color = 'bright_magenta'
        console.print(Panel(response_text, title=_make_panel_title(model_name, color),
                            border_style=color, box=box.ROUNDED))


@click.command()
@click.argument('query')
def lookup(query):
    """Look up a ticker by company name: buffet-bot lookup Apple"""
    try:
        results = yf.Search(query).quotes
    except Exception as e:
        console.print(f"[bright_red]Search error: {e}[/bright_red]")
        return

    if not results:
        console.print(f"[bright_yellow]No results found for '{query}'.[/bright_yellow]")
        return

    table = Table(title=f"Search results for: {query}", box=box.ROUNDED, header_style="bold bright_cyan")
    table.add_column("Symbol", style="bold bright_cyan")
    table.add_column("Company Name", style="bright_white")
    table.add_column("Exchange", style="dim bright_white")
    table.add_column("Type", style="dim bright_white")

    for q in results:
        table.add_row(
            q.get('symbol', ''),
            q.get('longname') or q.get('shortname', ''),
            q.get('exchange', ''),
            q.get('quoteType', ''),
        )

    console.print(table)
    console.print("\n[dim bright_cyan]Tip: Run [bold]buffet-bot analyze <SYMBOL>[/bold] to analyze any ticker above.[/dim bright_cyan]")


@click.command()
@click.argument('query', required=False, default=None, metavar='[QUERY]')
@click.option('--sector', default=None, help='Filter by sector.')
@click.option('--limit', default=50, show_default=True, type=int, help='Max companies to display.')
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
    from buffet_bot.universe import (
        list_companies, search_companies, search_edgar, SECTORS, _COMPANY_DB as _DB,
    )
    _TIP = "\n[dim bright_cyan]Tip: run [bold]buffet-bot analyze TICKER[/bold] or [bold]buffet-bot lookup TICKER[/bold] for detail.[/dim bright_cyan]"

    if full_universe:
        if not query:
            console.print("[bright_yellow]Provide a search term with --all, e.g.: buffet-bot browse apple --all[/bright_yellow]")
            return
        console.print(Panel(
            f"Searching SEC EDGAR for [bold bright_white]{query}[/bold bright_white] across [bold bright_cyan]10,000+[/bold bright_cyan] companies...",
            title=_make_panel_title("SEC EDGAR Universe Search", "bright_cyan"),
            border_style="bright_cyan", box=box.ROUNDED,
        ))
        with console.status("[dim]Fetching EDGAR company list...[/dim]", spinner="dots"):
            try:
                results = search_edgar(query, limit=limit)
            except Exception as e:
                console.print(f"[bright_red]EDGAR search failed: {e}[/bright_red]")
                return
        if not results:
            console.print(f"[bright_yellow]No EDGAR results for '{query}'.[/bright_yellow]")
            return
        tbl = Table(
            title=f"EDGAR Results for '{query}' ({len(results)} shown)",
            box=box.ROUNDED, header_style="bold bright_cyan",
        )
        tbl.add_column("Ticker",  style="bold bright_cyan", min_width=8,  no_wrap=True)
        tbl.add_column("Company Name", style="bright_white",               min_width=35)
        tbl.add_column("CIK",     style="dim bright_white",       min_width=10, no_wrap=True)
        for r in results:
            tbl.add_row(r["ticker"], r["name"].title(), r["cik"])
        console.print(tbl)
        console.print(f"[dim bright_cyan]Source: SEC EDGAR company_tickers.json — {len(results)} match(es)[/dim bright_cyan]")
        console.print(_TIP)
        return

    if query and not sector:
        results = search_companies(query, limit=limit)
        if not results:
            console.print(f"[dim]No bundled results for '{query}' — trying yfinance search...[/dim]")
            try:
                yf_results = yf.Search(query).quotes
                if not yf_results:
                    console.print(f"[bright_yellow]No results found for '{query}'. Try --all for the full EDGAR universe.[/bright_yellow]")
                    return
                tbl = Table(
                    title=f"Search results: '{query}'",
                    box=box.ROUNDED, header_style="bold bright_cyan",
                )
                tbl.add_column("Ticker",       style="bold bright_cyan", min_width=8)
                tbl.add_column("Company Name", style="bright_white",                    min_width=30)
                tbl.add_column("Exchange",     style="dim bright_white",       min_width=10)
                tbl.add_column("Type",         style="dim bright_white",       min_width=10)
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
                console.print(f"[bright_yellow]Search unavailable: {e}. Try --all for the full EDGAR universe.[/bright_yellow]")
                return
        tbl = Table(
            title=f"Search results: '{query}'  ({len(results)} match(es))",
            box=box.ROUNDED, header_style="bold bright_cyan",
        )
        tbl.add_column("Ticker",   style="bold bright_cyan", min_width=8,  no_wrap=True)
        tbl.add_column("Company", style="bright_white",                    min_width=35)
        tbl.add_column("Sector",   style="dim bright_white",       min_width=22)
        tbl.add_column("Exchange", style="dim bright_white",       min_width=8)
        for r in results:
            tbl.add_row(r["ticker"], r["name"], r["sector"], r["exchange"])
        console.print(tbl)
        console.print(_TIP)
        return

    if sector:
        companies = list_companies(sector=sector, limit=limit)
        tbl = Table(
            title=f"[bold bright_cyan]{sector}[/bold bright_cyan] — {len(companies)} companies",
            box=box.ROUNDED, header_style="bold bright_cyan",
        )
        tbl.add_column("#",        style="dim",       justify="right", min_width=4)
        tbl.add_column("Ticker",   style="bold bright_cyan", min_width=8,  no_wrap=True)
        tbl.add_column("Company", style="bright_white",                    min_width=38)
        tbl.add_column("Exchange", style="dim bright_white",       min_width=8)
        for i, c in enumerate(companies, 1):
            tbl.add_row(str(i), c["ticker"], c["name"], c["exchange"])
        console.print(tbl)
        console.print(_TIP)
        return

    sector_counts: dict[str, int] = {}
    for v in _DB.values():
        sector_counts[v["sector"]] = sector_counts.get(v["sector"], 0) + 1

    tbl = Table(
        title=f"[bold bright_cyan]Investable Universe[/bold bright_cyan]  —  {len(_DB)} companies across {len(SECTORS)} sectors",
        box=box.ROUNDED, header_style="bold bright_cyan",
    )
    tbl.add_column("Sector", style="bright_white",          min_width=28)
    tbl.add_column("Companies", style="bright_white",       justify="right", min_width=11)
    tbl.add_column("Sample Tickers",  style="dim bright_cyan",     min_width=35)

    for s in SECTORS:
        count   = sector_counts.get(s, 0)
        samples = [t for t, v in _DB.items() if v["sector"] == s][:5]
        tbl.add_row(f"[bold bright_cyan]{s}[/bold bright_cyan]", str(count), ", ".join(samples))

    console.print(tbl)
    console.print(
        "\n[dim bright_cyan]Filter by sector:  [bold]buffet-bot browse --sector Technology[/bold]\n"
        "Search by keyword:  [bold]buffet-bot browse \"electric vehicle\"[/bold]\n"
        "Full universe:      [bold]buffet-bot browse \"bank\" --all[/bold]  "
        f"(10,000+ companies via SEC EDGAR)[/dim bright_cyan]"
    )


@click.command()
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

    if is_crypto_symbol(ticker):
        _analyze_crypto(ticker, dry_run, primary_model, console, MODELS, MODEL_COLORS)
        return

    if not as_json:
        console.print(Panel(
            f"[bold bright_white]{ticker}[/bold bright_white]  |  Risk: [bright_yellow]{risk}[/bright_yellow]  |  Strategy: [bright_cyan]{strategy}[/bright_cyan]",
            title=_make_panel_title("Analyzing", "bright_cyan"), border_style="bright_cyan", box=box.ROUNDED))

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
            f"[bold bright_yellow]Earnings in {days_away} day(s)[/bold bright_yellow] "
            f"({timing}) — {earnings['fiscal_quarter']}  EPS est: {earnings['eps_forecast']}",
            title=_make_panel_title("Upcoming Earnings Warning", "bright_yellow"),
            border_style="bright_yellow", box=box.ROUNDED,
        ))
    analyst = result.get('analyst', {})
    if analyst:
        upside = analyst.get('upside_pct')
        sign   = '+' if (upside or 0) >= 0 else ''
        u_color = 'bright_green' if (upside or 0) >= 5 else ('bright_red' if (upside or 0) < 0 else 'bright_yellow')
        rating  = analyst.get('rating_key', 'N/A')
        r_color = ('bright_green' if rating in ('BUY', 'STRONG BUY') else
                   'bright_red'   if rating in ('SELL', 'UNDERPERFORM') else 'bright_yellow')
        changes_str = ''
        for ch in analyst.get('recent_changes', [])[:3]:
            changes_str += f"\n  {ch['firm']}: {ch['from'] or '?'} → {ch['to']} ({ch['action']})"
        console.print(Panel(
            f"Consensus:  [{r_color}][bold]{rating}[/bold][/{r_color}]"
            f"  [dim](mean score {analyst.get('rating_mean', '?')}/5.0, "
            f"{analyst.get('num_analysts', '?')} analysts)[/dim]\n"
            f"Price target: [bold bright_white]${analyst.get('target_mean', 'N/A')}[/bold bright_white]  "
            f"[dim](low ${analyst.get('target_low', '?')} — high ${analyst.get('target_high', '?')})[/dim]\n"
            f"Implied upside: [{u_color}][bold]{sign}{upside}%[/bold][/{u_color}]"
            + (f"\nRecent changes:{changes_str}" if changes_str else ''),
            title=_make_panel_title("Wall Street Analyst Consensus", r_color),
            border_style=r_color, box=box.ROUNDED,
        ))

    _print_ai_responses(result['responses'])
    console.print(f"\n[bright_cyan]CONSENSUS:[/bright_cyan] {_consensus_text(result['consensus'])}")
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
                beta_note  = f"  |  Beta: [bright_yellow]{sizing['beta']}x[/bright_yellow]" if sizing['beta'] != 1.0 else ""
                console.print(Panel(
                    f"Account cash: [bold bright_white]${cash:,.2f}[/bold bright_white]\n"
                    f"LLM suggested qty:   [dim]{llm_qty}[/dim]\n"
                    f"Formula qty:         [bold bright_green]{sizing['qty']}[/bold bright_green]  "
                    f"(${sizing['dollar_size']:,.2f})\n"
                    f"ATR: [bright_white]${sizing['atr']:.2f}[/bright_white]  ({sizing['atr_pct']}% of price){beta_note}",
                    title=_make_panel_title("Dynamic Position Sizing", "bright_green"),
                    border_style="bright_green", box=box.ROUNDED,
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


@click.command()
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
        f"[bold bright_white]{ticker}[/bold bright_white]  |  Risk: [bright_yellow]{risk}[/bright_yellow]  |  Strategy: [bright_cyan]{strategy}[/bright_cyan]",
        title=_make_panel_title("Analyzing", "bright_cyan"), border_style="bright_cyan", box=box.ROUNDED))

    result = _run_analysis(ticker, risk, primary_model, strategy)
    _print_live_market(ticker, result['realtime'], result['news'])
    _print_ai_responses(result['responses'])
    console.print(f"\n[bright_cyan]CONSENSUS:[/bright_cyan] {_consensus_text(result['consensus'])}")

    if result['consensus'] != 'BUY':
        console.print(f"[bright_yellow]Consensus is {result['consensus']} — no order placed.[/bright_yellow]")
        return

    if not result['best_buy_resp']:
        console.print("[bright_yellow]No valid BUY signal from models — no order placed.[/bright_yellow]")
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
            beta_note = f"  |  Beta: [bright_yellow]{sizing['beta']}x[/bright_yellow]" if sizing['beta'] != 1.0 else ""
            console.print(Panel(
                f"Account cash: [bold bright_white]${cash:,.2f}[/bold bright_white]\n"
                f"LLM suggested qty:   [dim]{llm_qty}[/dim]\n"
                f"Formula qty:         [bold bright_green]{sizing['qty']}[/bold bright_green]  "
                f"(${sizing['dollar_size']:,.2f})\n"
                f"ATR: [bright_white]${sizing['atr']:.2f}[/bright_white]  ({sizing['atr_pct']}% of price){beta_note}",
                title=_make_panel_title("Dynamic Position Sizing", "bright_green"),
                border_style="bright_green", box=box.ROUNDED,
            ))
    except Exception:
        sizing = None

    if click.confirm(f'Execute BUY {ticker}? (Paper)'):
        best = dict(result['best_buy_resp'])
        if sizing:
            best['qty'] = sizing['qty']
        _place_order(ticker, best)


@click.command()
@click.option('--limit', default=20, show_default=True, help='Max number of orders to show.')
@click.option('--ticker', default=None, help='Filter by ticker symbol.')
@click.option('--status', 'order_status', default='all',
              type=click.Choice(['all', 'open', 'closed']), show_default=True)
def history(limit, ticker, order_status):
    """Show past paper trades: buffet-bot history --ticker AAPL"""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus, OrderSide
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


@click.command()
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

    dates = [datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d') for ts in timestamps]
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


@click.command()
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def chat(primary_model):
    """Interactive multi-turn investing discussion with both AI models."""
    models_in_session = [primary_model]
    if primary_model != MODELS[1]:
        models_in_session.append(MODELS[1])

    histories = {m: [
        {'role': 'system', 'content':
         "You are an expert investing assistant guided by Warren Buffett's value investing principles. "
         "Be concise, insightful, and reference real financial data when possible."}
    ] for m in models_in_session}

    console.print(Panel(
        "[bold bright_cyan]Buffett AI Planning Session[/bold bright_cyan]\n\n"
        f"Models: {', '.join(f'[bold bright_white]{m}[/bold bright_white]' for m in models_in_session)}\n\n"
        "[dim]Type your question or topic. Both models will respond.\n"
        "Commands:  [bold]exit[/bold] or [bold]quit[/bold] to end  |  "
        "[bold]clear[/bold] to reset conversation history[/dim]",
        border_style="bright_cyan", box=box.ROUNDED,
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
                histories[m] = [histories[m][0]]
            console.print("[dim]Conversation history cleared.[/dim]")
            continue

        for m in models_in_session:
            histories[m].append({'role': 'user', 'content': user_input})

        for model in models_in_session:
            color = MODEL_COLORS.get(model, 'bright_cyan')
            if color == 'cyan':
                color = 'bright_cyan'
            elif color == 'magenta':
                color = 'bright_magenta'
            try:
                resp = ollama.chat(
                    model=model,
                    messages=histories[model],
                    options={'temperature': 0.5},
                )
                reply = resp['message']['content'].strip()
                histories[model].append({'role': 'assistant', 'content': reply})
                console.print(Panel(reply,
                                    title=_make_panel_title(model, color),
                                    border_style=color, box=box.ROUNDED))
            except Exception as e:
                console.print(f"[bright_red]{model} error: {e}[/bright_red]")
                histories[model].pop()


@click.command()
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
        title=f"[bold bright_green]Buffett Scan[/bold bright_green] — {scanned_at}",
        box=box.ROUNDED, header_style="bold bright_cyan",
    )
    table.add_column("Rank",  justify="right", style="dim bright_white")
    table.add_column("Ticker", style="bold bright_cyan", no_wrap=True)
    table.add_column("Score",  justify="right", style="bright_white")
    table.add_column("ROE%",   justify="right", style="bright_white")
    table.add_column("Debt/Eq", justify="right", style="bright_white")
    table.add_column("OpMgn%", justify="right", style="bright_white")
    table.add_column("P/E",    justify="right", style="bright_white")

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
            f"[{color}][bold]{score}[/bold][/{color}]",
            f"{roe}" if isinstance(roe, str) else f"{roe:.1f}",
            f"{debt}" if isinstance(debt, str) else f"{debt:.1f}",
            f"{margin}" if isinstance(margin, str) else f"{margin:.1f}",
            f"{pe}" if isinstance(pe, str) else f"{pe:.1f}",
        )

    console.print(table)
    console.print(f"[dim bright_cyan]Scanned {len(tickers)} tickers at {scanned_at}[/dim bright_cyan]")


@click.command()
def status():
    """Check account status — Alpaca paper, Coinbase, and IBKR if configured."""
    from buffet_bot.crypto import get_coinbase_balance
    from buffet_bot.ibkr import get_ibkr_status

    account = trading_client.get_account()
    console.print(Panel(
        f"Cash:          [bold bright_green]${float(account.cash):,.2f}[/bold bright_green]\n"
        f"Buying Power:  [bold bright_cyan]${float(account.buying_power):,.2f}[/bold bright_cyan]",
        title=_make_panel_title("Alpaca Paper Account", "bright_green"),
        border_style="bright_green", box=box.ROUNDED,
    ))

    cb_key = os.getenv("COINBASE_API_KEY")
    if cb_key:
        with console.status("[dim]Fetching Coinbase balance...[/dim]"):
            cb = get_coinbase_balance()
        if cb:
            rows = "\n".join(
                f"  {r['currency']}: [bold bright_white]{r['balance']:,.6f}[/bold bright_white]"
                for r in cb["accounts"]
            )
            console.print(Panel(
                f"[bold bright_green]USD Cash: ${cb['total_usd']:,.2f}[/bold bright_green]\n{rows}",
                title=_make_panel_title("Coinbase (Live)", "bright_yellow"),
                border_style="bright_yellow", box=box.ROUNDED,
            ))
        else:
            console.print("[dim]Coinbase: connected but could not fetch balances.[/dim]")
    else:
        console.print("[dim]Coinbase: not configured (set COINBASE_API_KEY to enable).[/dim]")

    ibkr_acct = os.getenv("IBKR_ACCOUNT_ID")
    if ibkr_acct:
        with console.status("[dim]Connecting to IBKR (TWS/IB Gateway)...[/dim]"):
            ibkr = get_ibkr_status()
        if ibkr:
            console.print(Panel(
                f"Net Liquidation: [bold bright_green]${ibkr.get('NetLiquidation', 0):,.2f}[/bold bright_green]\n"
                f"Total Cash:      [bold bright_cyan]${ibkr.get('TotalCashValue', 0):,.2f}[/bold bright_cyan]\n"
                f"Buying Power:    [bold bright_cyan]${ibkr.get('BuyingPower', 0):,.2f}[/bold bright_cyan]",
                title=_make_panel_title(f"IBKR — {ibkr.get('account', ibkr_acct)}", "bright_magenta"),
                border_style="bright_magenta", box=box.ROUNDED,
            ))
        else:
            console.print(
                "[dim]IBKR: could not connect — is TWS or IB Gateway running on "
                f"{os.getenv('IBKR_HOST', '127.0.0.1')}:{os.getenv('IBKR_PORT', '7497')}?[/dim]"
            )
    else:
        console.print("[dim]IBKR: not configured (set IBKR_ACCOUNT_ID to enable).[/dim]")


@click.command()
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


@click.command()
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


@click.command()
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

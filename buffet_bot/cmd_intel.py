"""CLI commands: news, insiders, crypto, volatile, options."""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import click
import ollama
import yfinance as yf
from rich.panel import Panel
from rich.table import Table
from rich import box

from buffet_bot.globals import (
    console, MODELS, MODEL_COLORS,
)
from buffet_bot.crypto import CRYPTO_SYMBOLS
from buffet_bot.data import (
    get_recent_news, get_realtime_data, get_tech_indicators, _complete_ticker,
)
from buffet_bot.display import _print_live_market
from buffet_bot.politicians import (
    fetch_house_trades, fetch_fmp_trades, merge_deduplicate, display_politician_trades,
)
from buffet_bot.insiders import (
    fetch_insider_transactions, display_insider_table,
)
from buffet_bot.crypto import (
    get_crypto_quote, get_crypto_volatility,
    analyze_crypto as _analyze_crypto,
)
from buffet_bot.volatile import scan_volatile, display_volatile_table, VOLATILE_UNIVERSE


@click.command()
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


@click.command()
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


@click.command()
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


@click.command()
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


@click.command()
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

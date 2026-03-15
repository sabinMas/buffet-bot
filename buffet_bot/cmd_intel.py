"""CLI commands: news, insiders, crypto, volatile, options, edge_scan."""
import os
import re
import json as _json
from concurrent.futures import ThreadPoolExecutor, as_completed

import click
import ollama
import yfinance as yf
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from buffet_bot.globals import (
    console, MODELS, MODEL_COLORS, GOAL_PRESETS, _CONFIG,
)
from buffet_bot.crypto import CRYPTO_SYMBOLS
from buffet_bot.data import (
    get_recent_news, get_realtime_data, get_tech_indicators, _complete_ticker,
)
from buffet_bot.display import _print_live_market, _make_panel_title, _bright_color
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
from buffet_bot.edge import compute_edge_score, DEFAULT_WEIGHTS
from buffet_bot.db import log_edge_scan, log_options_position, get_options_positions
from buffet_bot.macro import compute_macro_score
from buffet_bot.options_engine import (
    screen_covered_calls,
    find_optimal_csp,
    check_rolls_needed,
)


@click.command()
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--days', default=90, show_default=True, type=int,
              help='Look-back window for congressional trades (days).')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def news(ticker, days, primary_model):
    """News + congressional trade intelligence: buffet-bot news AAPL --days 60"""
    ticker = ticker.upper()
    console.print(Panel(
        f"[bold bright_white]{ticker}[/bold bright_white]  |  Congressional trade window: [bright_cyan]{days}d[/bright_cyan]",
        title=_make_panel_title("News & Politician Intelligence", "bright_cyan"), border_style="bright_cyan", box=box.ROUNDED,
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
        if beta       is not None: rows.append(f"Beta:               [bright_yellow]{beta:.2f}[/bright_yellow]")
        if short_pct  is not None: rows.append(f"Short % of Float:   [bright_yellow]{short_pct*100:.1f}%[/bright_yellow]")
        if short_ratio is not None: rows.append(f"Short Ratio (days): [bright_yellow]{short_ratio:.1f}[/bright_yellow]")
        if shares_short is not None: rows.append(f"Shares Short:       [dim bright_white]{shares_short:,}[/dim bright_white]")

        if rows:
            console.print(Panel("\n".join(rows), title=_make_panel_title("Short Interest & Beta", "bright_yellow"), border_style="bright_yellow", box=box.ROUNDED))
    except Exception:
        pass

    # 3) Congressional trades (House Stock Watcher + FMP merged)
    console.print(f"\n[bold bright_cyan]Congressional Trades — last {days} days[/bold bright_cyan]")
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
                color = _bright_color(MODEL_COLORS.get(primary_model, "bright_cyan"))
                console.print(Panel(
                    summary,
                    title=_make_panel_title(f"AI Sentiment Summary ({primary_model})", color),
                    border_style=color, box=box.ROUNDED,
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
        f"[bold bright_white]{ticker}[/bold bright_white]  |  Window: [bright_cyan]{days}d[/bright_cyan]  |  "
        f"Max rows: [bright_cyan]{limit}[/bright_cyan]",
        title=_make_panel_title("SEC EDGAR Form 4 — Insider Transactions", "bright_cyan"),
        border_style="bright_cyan", box=box.ROUNDED,
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


# ── Edge Scan ──────────────────────────────────────────────────────────────────


def _query_llm_conviction(ticker: str, model: str) -> float:
    """Ask a local LLM for a 0-100 Buffett-style conviction score for *ticker*.

    Returns 50.0 (neutral) on any failure so the edge score degrades gracefully.
    """
    prompt = (
        f"You are a Buffett-style value investor. Rate {ticker} as a long-term buy.\n"
        "Return ONLY valid JSON: {\"conviction\": <integer 0-100>}\n"
        "Where 0=strong sell, 50=neutral/no opinion, 100=strong buy.\n"
        "No prose, no markdown fences, no explanation."
    )
    try:
        resp = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.2},
        )
        raw = resp["message"]["content"].strip()
        # Strip deepseek-r1 <think>...</think> reasoning blocks
        raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
        # Direct parse
        try:
            data = _json.loads(raw)
        except _json.JSONDecodeError:
            start, end = raw.find('{'), raw.rfind('}')
            if start != -1 and end > start:
                data = _json.loads(raw[start:end + 1])
            else:
                return 50.0
        return max(0.0, min(100.0, float(data.get("conviction", 50))))
    except Exception:
        return 50.0


_EDGE_UNIVERSES = {
    'buffett':  GOAL_PRESETS.get('buffett',  []),
    'growth':   GOAL_PRESETS.get('growth',   []),
    'income':   GOAL_PRESETS.get('income',   []),
    'balanced': GOAL_PRESETS.get('balanced', []),
    'etf':      GOAL_PRESETS.get('etf',      []),
}


def _edge_score_bar(score: float, width: int = 20) -> str:
    """Return a Rich-coloured inline bar for a 0-100 score."""
    filled = round(score / 100 * width)
    bar    = '█' * filled + '░' * (width - filled)
    color  = 'bright_green' if score >= 70 else ('bright_yellow' if score >= 50 else 'bright_red')
    return f'[{color}]{bar}[/{color}]'


@click.command('edge-scan')
@click.option('--universe', 'preset', default='buffett', show_default=True,
              type=click.Choice(list(_EDGE_UNIVERSES.keys()) + ['watchlist']),
              help='Ticker universe to scan.')
@click.option('--tickers', multiple=True, metavar='TICKER',
              help='Override universe with explicit tickers (repeatable).')
@click.option('--min-edge', default=None, type=float,
              help='Minimum edge score to include in output (default: config edge.min_score).')
@click.option('--top', default=10, show_default=True, type=int,
              help='Maximum results to display.')
@click.option('--weights', default=None, type=str,
              help='JSON override for factor weights, e.g. \'{"insider":0.4,"buffett":0.3}\'.')
@click.option('--json', 'output_json', is_flag=True, default=False,
              help='Print raw JSON results instead of table.')
@click.option('--save/--no-save', default=True, show_default=True,
              help='Persist scan results to edge_scans table.')
@click.option('--llm', 'use_llm', is_flag=True, default=False,
              help='Query local LLM for a conviction score per ticker (fills the 20%% LLM weight).')
@click.option('--model', 'llm_model', default='qwen2.5:7b',
              type=click.Choice(MODELS), show_default=True,
              help='Ollama model to use for LLM conviction scoring.')
@click.option('--macro', 'use_macro', is_flag=True, default=False,
              help='Add a Macro Score column using FRED macro regime analysis.')
def edge_scan(preset, tickers, min_edge, top, weights, output_json, save, use_llm, llm_model, use_macro):
    """Multi-factor edge score scan: buffet-bot edge-scan --universe growth --top 5"""
    # ── Build ticker list ────────────────────────────────────────────────────
    if tickers:
        candidates = [t.upper() for t in tickers]
    elif preset == 'watchlist':
        from buffet_bot.db import get_watchlist
        candidates = [row['ticker'] for row in get_watchlist()]
        if not candidates:
            console.print(Panel(
                "[yellow]Your watchlist is empty.[/yellow]\n"
                "Add tickers with [bright_cyan]buffet-bot watchlist add AAPL[/bright_cyan]",
                title="[bold yellow]Empty Watchlist[/bold yellow]",
                border_style="yellow",
            ))
            return
    else:
        candidates = list(_EDGE_UNIVERSES.get(preset, []))

    if not candidates:
        console.print('[red]No tickers to scan.[/red]')
        return

    # ── Parse custom weights ─────────────────────────────────────────────────
    custom_weights: dict[str, float] | None = None
    if weights:
        try:
            custom_weights = {k: float(v) for k, v in _json.loads(weights).items()}
        except Exception:
            console.print(f'[red]Invalid --weights JSON: {weights}[/red]')
            return

    # ── Resolve min_edge ─────────────────────────────────────────────────────
    if min_edge is None:
        min_edge = float(_CONFIG.get('edge', {}).get('min_score', 60))

    label = f"custom ({len(candidates)})" if tickers else preset
    llm_label = f"[bright_magenta]{llm_model}[/bright_magenta]" if use_llm else "[dim]off[/dim]"
    macro_label = "[bright_green]on[/bright_green]" if use_macro else "[dim]off[/dim]"
    console.print(Panel(
        f"Universe: [bold bright_cyan]{label}[/bold bright_cyan]  |  "
        f"Tickers: [bold]{len(candidates)}[/bold]  |  "
        f"Min edge: [bold bright_yellow]{min_edge}[/bold bright_yellow]  |  "
        f"Top: [bold]{top}[/bold]  |  "
        f"LLM: {llm_label}  |  "
        f"Macro: {macro_label}",
        title="[bold bright_cyan]Multi-Factor Edge Scan[/bold bright_cyan]",
        border_style="bright_cyan",
    ))

    # ── Optional LLM conviction phase (sequential — one Ollama call at a time) ──
    llm_scores: dict[str, float] = {}
    if use_llm:
        console.print(f'[bright_magenta]Querying {llm_model} for conviction scores...[/bright_magenta]')
        for t in candidates:
            with console.status(f'[dim magenta]{llm_model} → {t}[/dim magenta]', spinner='dots'):
                llm_scores[t] = _query_llm_conviction(t, llm_model)

    # ── Concurrent scoring ───────────────────────────────────────────────────
    results: list[dict] = []
    errors: list[str] = []
    with console.status('[bright_cyan]Scoring tickers...[/bright_cyan]', spinner='dots'):
        with ThreadPoolExecutor(max_workers=4) as pool:
            future_map = {
                pool.submit(
                    compute_edge_score, t, custom_weights, None,
                    llm_scores.get(t, 50.0),
                ): t
                for t in candidates
            }
            for fut in as_completed(future_map):
                ticker = future_map[fut]
                try:
                    r = fut.result()
                    results.append(r)
                    if save and not use_macro:
                        log_edge_scan(r)
                except Exception as exc:
                    errors.append(f'{ticker}: {exc}')

    if errors:
        console.print(f'[dim red]Errors: {", ".join(errors)}[/dim red]')

    # ── Optional macro score phase (concurrent — uses cached FRED data) ───────
    macro_scores: dict[str, float] = {}
    if use_macro:
        with console.status('[bright_green]Computing macro scores...[/bright_green]', spinner='dots'):
            with ThreadPoolExecutor(max_workers=4) as pool:
                macro_future_map = {
                    pool.submit(compute_macro_score, t): t
                    for t in candidates
                }
                for fut in as_completed(macro_future_map):
                    t = macro_future_map[fut]
                    try:
                        macro_scores[t] = fut.result()
                    except Exception:
                        macro_scores[t] = 50.0
        # Save with macro_score stored in weights_json blob
        if save:
            for r in results:
                save_r = dict(r)
                weights_with_macro = dict(r.get('weights_used', {}))
                weights_with_macro['macro_score'] = macro_scores.get(r['ticker'], 50.0)
                save_r['weights_used'] = weights_with_macro
                log_edge_scan(save_r)

    # ── Filter + sort ────────────────────────────────────────────────────────
    passing = [r for r in results if r['edge_score'] >= min_edge]
    passing.sort(key=lambda r: r['edge_score'], reverse=True)
    passing = passing[:top]

    if output_json:
        console.print_json(_json.dumps(passing, indent=2))
        return

    if not passing:
        console.print(
            f'[yellow]No tickers scored >= {min_edge}. '
            f'Try lowering --min-edge or expanding your universe.[/yellow]'
        )
        return

    # ── Display table ────────────────────────────────────────────────────────
    tbl = Table(
        title=f'Edge Scan Results — {label}',
        box=box.ROUNDED,
        header_style='bold bright_cyan',
        show_lines=False,
    )
    tbl.add_column('Rank',       justify='right',  style='dim',            no_wrap=True)
    tbl.add_column('Ticker',     justify='left',   style='bold',           no_wrap=True)
    tbl.add_column('Edge Score', justify='right',  style='bold',           no_wrap=True)
    tbl.add_column('Bar',        justify='left',                           no_wrap=True)
    tbl.add_column('Buffett',    justify='right',  style='bright_cyan',    no_wrap=True)
    tbl.add_column('Insider',    justify='right',  style='bright_cyan',    no_wrap=True)
    tbl.add_column('Politician', justify='right',  style='bright_cyan',    no_wrap=True)
    tbl.add_column('Earnings',   justify='right',  style='bright_cyan',    no_wrap=True)
    tbl.add_column('Analyst',    justify='right',  style='bright_cyan',    no_wrap=True)
    if use_llm:
        tbl.add_column('LLM',    justify='right',  style='bright_magenta', no_wrap=True)
    if use_macro:
        tbl.add_column('Macro',  justify='right',  style='bright_green',   no_wrap=True)

    for rank, r in enumerate(passing, start=1):
        score = r['edge_score']
        c     = r['components']
        score_color = (
            'bright_green' if score >= 70
            else 'bright_yellow' if score >= 50
            else 'bright_red'
        )
        row = [
            str(rank),
            r['ticker'],
            f'[{score_color}]{score:.1f}[/{score_color}]',
            _edge_score_bar(score),
            f"{c.get('buffett',    50.0):.0f}",
            f"{c.get('insider',    50.0):.0f}",
            f"{c.get('politician', 50.0):.0f}",
            f"{c.get('earnings',   50.0):.0f}",
            f"{c.get('analyst',    50.0):.0f}",
        ]
        if use_llm:
            llm_val = c.get('llm', 50.0)
            llm_color = 'bright_green' if llm_val >= 70 else ('bright_yellow' if llm_val >= 50 else 'bright_red')
            row.append(f'[{llm_color}]{llm_val:.0f}[/{llm_color}]')
        if use_macro:
            ms = macro_scores.get(r['ticker'], 50.0)
            ms_color = 'bright_green' if ms >= 65 else ('bright_yellow' if ms >= 40 else 'bright_red')
            row.append(f'[{ms_color}]{ms:.0f}[/{ms_color}]')
        tbl.add_row(*row)

    console.print(tbl)

    w_used = passing[0]['weights_used'] if passing else DEFAULT_WEIGHTS
    weight_line = '  '.join(
        f'[dim]{k}[/dim]=[bright_cyan]{v:.0%}[/bright_cyan]'
        for k, v in w_used.items()
    )
    console.print(f'[dim]Weights:[/dim]  {weight_line}')
    llm_note = f'  |  LLM: [bright_magenta]{llm_model}[/bright_magenta]' if use_llm else ''
    macro_note = '  |  Macro: [bright_green]FRED[/bright_green]' if use_macro else ''
    console.print(
        f'[dim]{len(results)} scored  |  {len(passing)} passed min-edge {min_edge}  |  '
        f'{"saved to DB" if save else "not saved"}[/dim]{llm_note}{macro_note}'
    )


# ── Options Income Group ───────────────────────────────────────────────────────


def _build_candidates(preset: str, tickers: tuple) -> list[str]:
    """Resolve a ticker list from --universe preset or --tickers override."""
    if tickers:
        return [t.upper() for t in tickers]
    if preset == 'watchlist':
        from buffet_bot.db import get_watchlist
        return [row['ticker'] for row in get_watchlist()]
    return list(_EDGE_UNIVERSES.get(preset, []))


def _contract_table(results: list[dict], title: str, strategy: str) -> Table:
    """Build a Rich table for covered-call or cash-put results."""
    is_put = strategy == 'CSP'
    tbl = Table(title=title, box=box.ROUNDED, header_style='bold bright_yellow', show_lines=False)
    tbl.add_column('#',           justify='right',  style='dim',           no_wrap=True)
    tbl.add_column('Ticker',      justify='left',   style='bold',          no_wrap=True)
    tbl.add_column('Expiry',      justify='left',   style='bright_white',  no_wrap=True)
    tbl.add_column('Strike',      justify='right',  style='bright_white',  no_wrap=True)
    tbl.add_column('Mid',         justify='right',  style='bright_yellow', no_wrap=True)
    tbl.add_column('Ann Yield',   justify='right',  style='bold',          no_wrap=True)
    if is_put:
        tbl.add_column('Cash Req',    justify='right',  style='bright_cyan',   no_wrap=True)
    tbl.add_column('DTE',         justify='right',  style='bright_cyan',   no_wrap=True)
    tbl.add_column('Δ est',       justify='right',  style='dim',           no_wrap=True)
    tbl.add_column('IV',          justify='right',  style='dim',           no_wrap=True)
    tbl.add_column('OI',          justify='right',  style='dim',           no_wrap=True)

    for i, r in enumerate(results, start=1):
        yld = r.get('annual_yield', 0)
        yld_color = 'bright_green' if yld >= 0.15 else ('bright_yellow' if yld >= 0.08 else 'bright_red')
        iv_val = r.get('iv', 0)
        row = [
            str(i),
            r['ticker'],
            r.get('expiry', ''),
            f"${r.get('strike', 0):.2f}",
            f"${r.get('mid', 0):.2f}",
            f'[{yld_color}]{yld:.1%}[/{yld_color}]',
        ]
        if is_put:
            row.append(f"${r.get('cash_required', 0):,.0f}")
        row += [
            str(r.get('dte', 0)),
            f"{r.get('delta_est', 0):.2f}",
            f"{iv_val:.1%}" if iv_val else '—',
            f"{r.get('oi', 0):,}",
        ]
        tbl.add_row(*row)
    return tbl


@click.group('options-income')
def options_income():
    """Options income strategies: covered calls, cash-secured puts, roll check, dashboard."""
    pass


@options_income.command('covered-calls')
@click.option('--universe', 'preset', default='buffett', show_default=True,
              type=click.Choice(list(_EDGE_UNIVERSES.keys()) + ['watchlist']),
              help='Ticker universe to screen.')
@click.option('--tickers', multiple=True, metavar='TICKER',
              help='Override universe with explicit tickers (repeatable).')
@click.option('--delta', default=0.30, show_default=True, type=float,
              help='Target delta for call strikes (0.10–0.40).')
@click.option('--min-dte', default=21, show_default=True, type=int,
              help='Minimum days to expiration.')
@click.option('--max-dte', default=45, show_default=True, type=int,
              help='Maximum days to expiration.')
@click.option('--min-yield', default=0.08, show_default=True, type=float,
              help='Minimum annualized yield to include (e.g. 0.08 = 8%).')
@click.option('--top', default=10, show_default=True, type=int,
              help='Maximum results to display.')
@click.option('--save', 'do_save', is_flag=True, default=False,
              help='Log results to options_positions table for tracking.')
def covered_calls(preset, tickers, delta, min_dte, max_dte, min_yield, top, do_save):
    """Screen tickers for covered call opportunities: buffet-bot options-income covered-calls"""
    candidates = _build_candidates(preset, tickers)
    if not candidates:
        console.print('[red]No tickers to screen.[/red]')
        return

    label = f'custom ({len(candidates)})' if tickers else preset
    console.print(Panel(
        f'Universe: [bold bright_yellow]{label}[/bold bright_yellow]  |  '
        f'Tickers: [bold]{len(candidates)}[/bold]  |  '
        f'Target Δ: [bold]{delta:.2f}[/bold]  |  '
        f'DTE: [bold]{min_dte}–{max_dte}[/bold]  |  '
        f'Min yield: [bold bright_yellow]{min_yield:.0%}[/bold bright_yellow]',
        title='[bold bright_yellow]Covered Call Screener[/bold bright_yellow]',
        border_style='bright_yellow',
    ))

    with console.status('[bright_yellow]Scanning options chains...[/bright_yellow]', spinner='dots'):
        results = screen_covered_calls(
            candidates, target_delta=delta,
            min_dte=min_dte, max_dte=max_dte,
            min_annualized_yield=min_yield,
        )

    results = results[:top]
    if not results:
        console.print(
            f'[yellow]No covered call opportunities found for {label}. '
            f'Try lowering --min-yield or broadening --universe.[/yellow]'
        )
        return

    console.print(_contract_table(results, f'Covered Calls — {label}', 'CC'))

    if do_save:
        saved = 0
        for r in results:
            row_id = log_options_position(
                r['ticker'], 'COVERED_CALL', 'CALL',
                r['expiry'], r['strike'], r['mid'],
            )
            if row_id:
                saved += 1
        console.print(f'[dim]{saved} position(s) logged to options_positions table.[/dim]')

    console.print(
        f'[dim]{len(results)} opportunities  |  '
        f'{"tracked in DB" if do_save else "use --save to track"}  |  '
        f'--execute available in live mode only[/dim]'
    )


@options_income.command('cash-puts')
@click.option('--universe', 'preset', default='buffett', show_default=True,
              type=click.Choice(list(_EDGE_UNIVERSES.keys()) + ['watchlist']),
              help='Ticker universe to screen.')
@click.option('--tickers', multiple=True, metavar='TICKER',
              help='Override universe with explicit tickers (repeatable).')
@click.option('--min-edge', default=65.0, show_default=True, type=float,
              help='Minimum edge score to pass the pre-filter (0–100).')
@click.option('--delta', default=0.20, show_default=True, type=float,
              help='Target delta for put strikes (0.10–0.40).')
@click.option('--max-cash', default=50_000.0, show_default=True, type=float,
              help='Max cash collateral per contract (strike × 100).')
@click.option('--min-yield', default=0.06, show_default=True, type=float,
              help='Minimum annualized yield (e.g. 0.06 = 6%).')
@click.option('--top', default=10, show_default=True, type=int,
              help='Maximum results to display.')
@click.option('--save', 'do_save', is_flag=True, default=False,
              help='Log results to options_positions table for tracking.')
def cash_puts(preset, tickers, min_edge, delta, max_cash, min_yield, top, do_save):
    """Screen for cash-secured put opportunities on high-conviction tickers:
    buffet-bot options-income cash-puts --min-edge 70"""
    candidates = _build_candidates(preset, tickers)
    if not candidates:
        console.print('[red]No tickers to screen.[/red]')
        return

    label = f'custom ({len(candidates)})' if tickers else preset
    console.print(Panel(
        f'Universe: [bold bright_yellow]{label}[/bold bright_yellow]  |  '
        f'Min edge: [bold bright_cyan]{min_edge}[/bold bright_cyan]  |  '
        f'Target Δ: [bold]{delta:.2f}[/bold]  |  '
        f'Max cash: [bold]${max_cash:,.0f}[/bold]  |  '
        f'Min yield: [bold bright_yellow]{min_yield:.0%}[/bold bright_yellow]',
        title='[bold bright_yellow]Cash-Secured Put Screener[/bold bright_yellow]',
        border_style='bright_yellow',
    ))

    # Edge score pre-filter
    with console.status(f'[dim]Filtering {len(candidates)} tickers by edge score...[/dim]', spinner='dots'):
        with ThreadPoolExecutor(max_workers=4) as pool:
            edge_futures = {pool.submit(compute_edge_score, t): t for t in candidates}
            qualified: list[str] = []
            for fut in as_completed(edge_futures):
                try:
                    r = fut.result()
                    if r.get('edge_score', 0) >= min_edge:
                        qualified.append(r['ticker'])
                except Exception:
                    pass

    if not qualified:
        console.print(
            f'[yellow]No tickers passed the edge score filter (>= {min_edge}). '
            f'Try lowering --min-edge.[/yellow]'
        )
        return

    console.print(f'[dim]{len(qualified)}/{len(candidates)} passed edge filter: {", ".join(qualified)}[/dim]')

    # Screen CSPs concurrently
    csp_min_dte, csp_max_dte = 21, 45
    results: list[dict] = []
    with console.status('[bright_yellow]Scanning options chains...[/bright_yellow]', spinner='dots'):
        with ThreadPoolExecutor(max_workers=4) as pool:
            from buffet_bot.data import get_realtime_data as _rtd
            future_map = {}
            for t in qualified:
                live = _rtd(t)
                price = live.get('price', 0)
                if price:
                    future_map[pool.submit(
                        find_optimal_csp, t, price, delta, csp_min_dte, csp_max_dte,
                        max_cash, min_yield,
                    )] = t

            for fut in as_completed(future_map):
                try:
                    r = fut.result()
                    if r:
                        results.append(r)
                except Exception:
                    pass

    results.sort(key=lambda r: r.get('annual_yield', 0), reverse=True)
    results = results[:top]

    if not results:
        console.print(
            f'[yellow]No cash-secured put opportunities found. '
            f'Try lowering --min-yield or --min-edge.[/yellow]'
        )
        return

    console.print(_contract_table(results, f'Cash-Secured Puts — {label}', 'CSP'))

    if do_save:
        saved = 0
        for r in results:
            row_id = log_options_position(
                r['ticker'], 'CASH_SECURED_PUT', 'PUT',
                r['expiry'], r['strike'], r['mid'],
            )
            if row_id:
                saved += 1
        console.print(f'[dim]{saved} position(s) logged to options_positions table.[/dim]')

    console.print(
        f'[dim]{len(results)} opportunities  |  '
        f'{"tracked in DB" if do_save else "use --save to track"}  |  '
        f'--execute available in live mode only[/dim]'
    )


@options_income.command('dashboard')
@click.option('--all', 'show_all', is_flag=True, default=False,
              help='Include closed positions in addition to open ones.')
def options_dashboard(show_all):
    """Open options positions + 12-month realized income chart:
    buffet-bot options-income dashboard"""
    import plotext as _plt

    status_filter = 'ALL' if show_all else 'OPEN'
    positions = get_options_positions(status=status_filter)

    console.print(Panel(
        f'Showing [bold]{"all" if show_all else "open"}[/bold] positions  |  '
        f'Total: [bold]{len(positions)}[/bold]',
        title='[bold bright_yellow]Options Income Dashboard[/bold bright_yellow]',
        border_style='bright_yellow',
    ))

    if not positions:
        console.print(
            '[yellow]No options positions found. '
            'Use [bright_cyan]options-income covered-calls --save[/bright_cyan] '
            'or [bright_cyan]options-income cash-puts --save[/bright_cyan] to track positions.[/yellow]'
        )
        return

    # Positions table
    tbl = Table(
        title='Options Positions', box=box.ROUNDED,
        header_style='bold bright_yellow', show_lines=False,
    )
    tbl.add_column('#',          justify='right',  style='dim',            no_wrap=True)
    tbl.add_column('Ticker',     justify='left',   style='bold',           no_wrap=True)
    tbl.add_column('Strategy',   justify='left',   style='bright_yellow',  no_wrap=True)
    tbl.add_column('Type',       justify='left',   style='dim',            no_wrap=True)
    tbl.add_column('Strike',     justify='right',  style='bright_white',   no_wrap=True)
    tbl.add_column('Expiry',     justify='left',   style='bright_white',   no_wrap=True)
    tbl.add_column('DTE',        justify='right',  style='bright_cyan',    no_wrap=True)
    tbl.add_column('Premium',    justify='right',  style='bright_yellow',  no_wrap=True)
    tbl.add_column('Qty',        justify='right',  style='dim',            no_wrap=True)
    tbl.add_column('P&L',        justify='right',  style='bold',           no_wrap=True)
    tbl.add_column('Status',     justify='left',   style='dim',            no_wrap=True)

    from datetime import date as _date
    today = _date.today()
    for i, p in enumerate(positions, start=1):
        try:
            exp = _date.fromisoformat(p['expiry'])
            dte = (exp - today).days
            dte_str = str(dte) if dte >= 0 else '[red]expired[/red]'
        except Exception:
            dte_str = '—'

        pnl = p.get('pnl', 0)
        pnl_color = 'bright_green' if pnl > 0 else ('bright_red' if pnl < 0 else 'dim')
        pnl_str = f'[{pnl_color}]${pnl:+,.2f}[/{pnl_color}]' if p['status'] == 'CLOSED' else '—'

        status_color = 'bright_green' if p['status'] == 'OPEN' else 'dim'
        tbl.add_row(
            str(i),
            p['ticker'],
            p['strategy'],
            p['option_type'],
            f"${p['strike']:.2f}",
            p['expiry'],
            dte_str,
            f"${p['premium_received']:.2f}",
            str(p['contracts']),
            pnl_str,
            f'[{status_color}]{p["status"]}[/{status_color}]',
        )

    console.print(tbl)

    # 12-month income bar chart from closed positions
    closed = [p for p in positions if p['status'] == 'CLOSED' and p.get('pnl', 0) != 0]
    if closed:
        monthly: dict[str, float] = {}
        for p in closed:
            try:
                month_key = p['closed_at'][:7]  # YYYY-MM
                monthly[month_key] = monthly.get(month_key, 0.0) + p.get('pnl', 0)
            except Exception:
                pass

        if monthly:
            sorted_months = sorted(monthly.keys())[-12:]
            labels = [m[5:] for m in sorted_months]   # MM only
            values = [monthly[m] for m in sorted_months]
            total_income = sum(values)

            _plt.clf()
            _plt.bar(labels, values, color='yellow')
            _plt.title('Realized Income by Month (last 12 months)')
            _plt.xlabel('Month')
            _plt.ylabel('P&L ($)')
            _plt.show()
            console.print(
                f'[bold bright_yellow]Total realized income:[/bold bright_yellow] '
                f'[bold]${total_income:+,.2f}[/bold]'
            )


@options_income.command('roll-check')
@click.option('--dte-threshold', default=7, show_default=True, type=int,
              help='Flag positions with DTE at or below this value.')
def roll_check(dte_threshold):
    """Flag open options positions approaching expiration:
    buffet-bot options-income roll-check"""
    positions = get_options_positions(status='OPEN')
    if not positions:
        console.print('[yellow]No open options positions to check.[/yellow]')
        return

    console.print(Panel(
        f'Open positions: [bold]{len(positions)}[/bold]  |  '
        f'DTE threshold: [bold bright_red]{dte_threshold}[/bold bright_red]',
        title='[bold bright_red]Options Roll Check[/bold bright_red]',
        border_style='bright_red',
    ))

    with console.status('[dim]Checking positions...[/dim]', spinner='dots'):
        flagged = check_rolls_needed(positions, dte_threshold=dte_threshold)

    if not flagged:
        console.print(
            f'[bright_green]All positions have more than {dte_threshold} DTE. '
            f'No rolls needed.[/bright_green]'
        )
        return

    tbl = Table(
        title=f'{len(flagged)} Position(s) Needing Attention',
        box=box.ROUNDED, header_style='bold bright_red', show_lines=False,
    )
    tbl.add_column('Ticker',      justify='left',  style='bold',         no_wrap=True)
    tbl.add_column('Strategy',    justify='left',  style='bright_yellow', no_wrap=True)
    tbl.add_column('Strike',      justify='right', style='bright_white',  no_wrap=True)
    tbl.add_column('Expiry',      justify='left',  style='bright_white',  no_wrap=True)
    tbl.add_column('DTE',         justify='right', style='bright_red',    no_wrap=True)
    tbl.add_column('Action',      justify='left',  style='bold',          no_wrap=True)
    tbl.add_column('New Strike',  justify='right', style='bright_cyan',   no_wrap=True)
    tbl.add_column('ATR',         justify='right', style='dim',           no_wrap=True)

    action_colors = {
        'ROLL_UP':    'bright_green',
        'ROLL_DOWN':  'bright_yellow',
        'LET_EXPIRE': 'bright_cyan',
        'CLOSE':      'bright_red',
    }
    for item in flagged:
        pos = item['position']
        action = item['action']
        color = action_colors.get(action, 'white')
        new_strike = item.get('new_strike')
        atr = item.get('atr')
        tbl.add_row(
            pos.get('ticker', ''),
            pos.get('strategy', ''),
            f"${float(pos.get('strike', 0)):.2f}",
            pos.get('expiry', ''),
            str(item['dte_remaining']),
            f'[{color}]{action}[/{color}]',
            f'${new_strike:.2f}' if new_strike else '—',
            f'${atr:.2f}' if atr else '—',
        )

    console.print(tbl)
    console.print(
        '[dim]ROLL_UP/ROLL_DOWN: roll the position out in time with new strike  |  '
        'LET_EXPIRE: collect full premium  |  '
        'CLOSE: buy back to avoid assignment risk[/dim]'
    )

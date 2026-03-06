"""CLI commands: rebalance, backtest, correlate, check_sells, var, forecast, whatif, scenarios, milestones, sectors."""
import itertools
from datetime import datetime, timedelta

import click
import numpy as np
import pandas as pd
import yfinance as yf
import plotext as plt
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich import box

from buffet_bot.globals import console, MODELS, trading_client, LIVE_MODE
from buffet_bot.live_guard import confirm_live_execution
from buffet_bot.data import _complete_ticker
from buffet_bot.display import _score_color, _make_panel_title
from buffet_bot.risk import (
    _check_sell_signals, _show_sector_table, _calculate_portfolio_var,
)
from buffet_bot.projections import (
    _calculate_future_value, _get_portfolio_expected_return, _get_ai_expected_return,
    _run_monte_carlo, _display_mc_chart, _years_to_reach,
)
from buffet_bot.backtest import _run_backtest


@click.command()
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
        console.print("[bright_yellow]No open positions to rebalance.[/bright_yellow]")
        return

    cash = float(account.cash)
    position_value = sum(float(p.market_value) for p in positions)
    total_value = position_value + cash if include_cash else position_value

    if total_value <= 0:
        console.print("[bright_red]Portfolio value is zero.[/bright_red]")
        return

    n = len(positions)
    target_pct = 1.0 / n
    target_value = total_value * target_pct

    table = Table(title=f"[bold bright_cyan]Rebalance Analysis[/bold bright_cyan] (Equal Weight)", box=box.ROUNDED,
                  header_style="bold bright_cyan")
    table.add_column("Ticker",   style="bold bright_cyan", no_wrap=True)
    table.add_column("Current $",  justify="right", style="bright_white")
    table.add_column("Current %",  justify="right", style="bright_white")
    table.add_column("Target %",   justify="right", style="bright_white")
    table.add_column("Diff %",     justify="right", style="bright_white")
    table.add_column("Action", style="bright_white")
    table.add_column("Shares",     justify="right", style="bright_white")

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
        f"Positions: [bold bright_white]{n}[/bold bright_white]  |  "
        f"Position value: [bold bright_white]${position_value:,.2f}[/bold bright_white]  |  "
        f"Cash: [bold bright_white]${cash:,.2f}[/bold bright_white]  |  "
        f"Target per position: [bold bright_white]${target_value:,.2f}[/bold bright_white] ({target_pct:.1%})",
        title=_make_panel_title("Portfolio Summary", "bright_cyan"), border_style="bright_cyan", box=box.ROUNDED,
    ))
    console.print(table)

    if execute and buys:
        console.print(f"\n[bold bright_cyan]Placing {len(buys)} buy order(s)...[/bold bright_cyan]")
        mode_label = "LIVE" if LIVE_MODE else "Paper"
        for symbol, shares, price in buys:
            if click.confirm(f"  BUY {shares}x {symbol} @ ~${price:.2f} ({mode_label})?", default=False):
                if confirm_live_execution(
                    f"BUY {shares}x {symbol}", symbol, shares, "BUY",
                    estimated_cost=shares * price,
                ):
                    try:
                        order = MarketOrderRequest(
                            symbol=symbol, qty=shares,
                            side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
                        )
                        result = trading_client.submit_order(order)
                        console.print(f"  [bright_green]Order submitted: {result.id}[/bright_green]")
                    except Exception as e:
                        console.print(f"  [bright_red]Order error: {e}[/bright_red]")
    elif execute:
        console.print("[dim]No ADD trades needed — portfolio is balanced.[/dim]")
    else:
        console.print("[dim bright_cyan]Dry run. Use --execute to place paper buy orders for ADD signals.[/dim bright_cyan]")


@click.command()
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--period',  default=1.0,     show_default=True, type=float, help='Period in years.')
@click.option('--capital', default=10000.0, show_default=True, type=float, help='Starting capital ($).')
@click.option('--compare/--no-compare', default=True, show_default=True,
              help='Compare against SPY buy-and-hold benchmark.')
def backtest(ticker, period, capital, compare):
    """Backtest RSI strategy on a ticker: buffet-bot backtest AAPL --period 2 --capital 10000"""
    ticker = ticker.upper()
    console.print(Panel(
        f"[bold bright_white]{ticker}[/bold bright_white]  |  Period: {period:.1f}yr  |  Capital: [bright_green]${capital:,.0f}[/bright_green]",
        title=_make_panel_title("Backtesting", "bright_cyan"), border_style="bright_cyan", box=box.ROUNDED))

    with console.status("[dim]Running backtest...[/dim]"):
        metrics, equity_curve, dates, spy_metrics = _run_backtest(
            ticker, period, capital, compare_spy=compare)

    if metrics is None:
        console.print("[bright_red]Backtest failed — insufficient data (need ≥60 bars).[/bright_red]")
        return

    tbl = Table(title=f"[bold bright_cyan]Backtest Results[/bold bright_cyan] — {ticker}", box=box.ROUNDED, header_style="bold bright_cyan")
    tbl.add_column("Metric", style="bold bright_white")
    tbl.add_column(ticker, justify="right", style="bright_white")
    if spy_metrics:
        tbl.add_column("SPY (B&H)", justify="right", style="dim bright_white")

    def _mrow(label, key, fmt_fn, color_fn):
        val     = metrics.get(key)
        val_str = fmt_fn(val) if val is not None else "—"
        color   = color_fn(val) if val is not None else "bright_white"
        row     = [label, f"[{color}]{val_str}[/{color}]"]
        if spy_metrics:
            sv      = spy_metrics.get(key)
            row.append(fmt_fn(sv) if sv is not None else "—")
        tbl.add_row(*row)

    _mrow("Total Return",  "total_return",  lambda v: f"{v:.1%}",
          lambda v: "bright_green" if v > 0 else "bright_red")
    _mrow("CAGR",          "cagr",          lambda v: f"{v:.1%}",
          lambda v: "bright_green" if v > 0.09 else "bright_yellow" if v > 0 else "bright_red")
    _mrow("Sharpe Ratio",  "sharpe",        lambda v: f"{v:.2f}",
          lambda v: "bright_green" if v > 1 else "bright_yellow" if v > 0.5 else "bright_red")
    _mrow("Max Drawdown",  "max_drawdown",  lambda v: f"{v:.1%}",
          lambda v: "bright_green" if v < 0.20 else "bright_yellow" if v < 0.30 else "bright_red")
    _mrow("Win Rate",      "win_rate",      lambda v: f"{v:.1%}",
          lambda v: "bright_green" if v >= 0.5 else "bright_yellow")
    _mrow("Avg Win ($)",   "avg_win",       lambda v: f"${v:.2f}",  lambda v: "bright_green")
    _mrow("Avg Loss ($)",  "avg_loss",      lambda v: f"${v:.2f}",  lambda v: "bright_red")
    _mrow("Profit Factor", "profit_factor",
          lambda v: f"{v:.2f}" if v != float('inf') else "∞",
          lambda v: "bright_green" if v >= 1.5 else "bright_yellow" if v >= 1 else "bright_red")
    _mrow("# Trades",      "n_trades",      lambda v: str(int(v)),  lambda v: "bright_white")

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


@click.command()
def correlate():
    """Correlation matrix for current portfolio: buffet-bot correlate"""
    try:
        positions = trading_client.get_all_positions()
    except Exception as e:
        console.print(f"[bright_red]Could not fetch positions: {e}[/bright_red]")
        return

    if len(positions) < 2:
        console.print("[bright_yellow]Need at least 2 open positions for correlation analysis.[/bright_yellow]")
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
        console.print(f"[bright_red]Correlation error: {e}[/bright_red]")
        return

    valid_tickers = [t for t in tickers if t in corr.columns]

    tbl = Table(title=f"[bold bright_cyan]Correlation Matrix[/bold bright_cyan] (6-month daily returns)",
                box=box.ROUNDED, header_style="bold bright_cyan")
    tbl.add_column("", style="bold bright_cyan")
    for t in valid_tickers:
        tbl.add_column(t, justify="right", style="bright_white")

    for t1 in valid_tickers:
        row = [t1]
        for t2 in valid_tickers:
            if t1 == t2:
                row.append("[dim]1.00[/dim]")
            else:
                val   = float(corr.loc[t1, t2])
                color = "bright_green" if abs(val) < 0.3 else "bright_yellow" if abs(val) < 0.6 else "bright_red"
                row.append(f"[{color}]{val:.2f}[/{color}]")
        tbl.add_row(*row)

    console.print(tbl)

    pairs = list(itertools.combinations(valid_tickers, 2))
    if pairs:
        avg_abs   = float(np.mean([abs(corr.loc[t1, t2]) for t1, t2 in pairs]))
        diversity = 1 - avg_abs
        color     = "bright_green" if diversity > 0.7 else "bright_yellow" if diversity > 0.4 else "bright_red"
        console.print(
            f"\n[bold bright_cyan]Diversity Score:[/bold bright_cyan] [{color}]{diversity:.2f}[/{color}]  "
            "[dim](1.0 = fully uncorrelated  0.0 = identical moves)[/dim]")

    _show_sector_table(valid_tickers)


@click.command('check-sells')
@click.option('--execute', is_flag=True, default=False,
              help='Sell positions flagged STOP or THESIS_BROKEN.')
@click.option('--tlh-threshold', 'tlh_pct', default=5.0, show_default=True, type=float,
              help='Minimum loss % to flag a TAX_LOSS harvest candidate (default 5.0).')
def check_sells(execute, tlh_pct):
    """Check open positions for sell signals: buffet-bot check-sells [--execute]"""
    try:
        positions = trading_client.get_all_positions()
    except Exception as e:
        console.print(f"[bright_red]Could not fetch positions: {e}[/bright_red]")
        return

    if not positions:
        console.print("[bright_yellow]No open positions.[/bright_yellow]")
        return

    console.print(f"[dim]Checking {len(positions)} position(s) for sell signals...[/dim]")
    with console.status("[dim]Analyzing signals...[/dim]"):
        results = _check_sell_signals(positions, tlh_pct=tlh_pct)

    tbl = Table(title=f"[bold bright_red]Sell Signal Analysis[/bold bright_red]", box=box.ROUNDED, header_style="bold bright_cyan")
    tbl.add_column("Ticker",  style="bold bright_cyan")
    tbl.add_column("Entry",   justify="right", style="bright_white")
    tbl.add_column("Current", justify="right", style="bright_white")
    tbl.add_column("P&L%",    justify="right", style="bright_white")
    tbl.add_column("B.Score", justify="right", style="bright_white")
    tbl.add_column("RSI",     justify="right", style="bright_white")
    tbl.add_column("Signals", style="bright_white")
    tbl.add_column("Rec.", style="bright_white")

    sell_flagged = []
    for pos, signals, b_score, rsi_val in results:
        try:
            entry   = float(pos.avg_entry_price)
            current = float(pos.current_price)
            pnl_pct = (current - entry) / entry * 100 if entry else 0.0
        except Exception:
            entry = current = pnl_pct = 0.0

        pnl_color = "bright_green" if pnl_pct >= 0 else "bright_red"
        s_color   = _score_color(b_score)
        sig_text  = ", ".join(signals) if signals else "—"

        if {'STOP', 'THESIS_BROKEN'} & set(signals):
            rec = "[bold bright_red]SELL[/bold bright_red]"
            sell_flagged.append(pos)
        elif signals:
            rec = "[bold bright_yellow]REVIEW[/bold bright_yellow]"
        else:
            rec = "[bold bright_green]HOLD[/bold bright_green]"

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
        console.print(f"\n[bold bright_red]Selling: {', '.join(p.symbol for p in sell_flagged)}[/bold bright_red]")
        mode_label = "LIVE" if LIVE_MODE else "Paper"
        if click.confirm(f"Confirm sells? ({mode_label})"):
            for pos in sell_flagged:
                qty = int(float(pos.qty))
                if confirm_live_execution(
                    f"SELL {qty}x {pos.symbol}", pos.symbol, qty, "SELL",
                ):
                    try:
                        order  = MarketOrderRequest(
                            symbol=pos.symbol,
                            qty=qty,
                            side=OrderSide.SELL,
                            time_in_force=TimeInForce.DAY,
                        )
                        result = trading_client.submit_order(order)
                        console.print(f"[bright_green]SELL {pos.symbol} submitted: {result.id}[/bright_green]")
                    except Exception as e:
                        console.print(f"[bright_red]Error selling {pos.symbol}: {e}[/bright_red]")
    elif sell_flagged and not execute:
        console.print("\n[dim bright_cyan]To execute these sells: buffet-bot check-sells --execute[/dim bright_cyan]")


@click.command()
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
        console.print(f"[bright_red]Could not fetch positions: {e}[/bright_red]")
        return

    if not positions:
        console.print("[bright_yellow]No open positions. Open some trades first.[/bright_yellow]")
        return

    pct_str = f"{int(confidence * 100)}%"
    with console.status(f"[dim]Fetching {days}d price history for VaR...[/dim]"):
        result = _calculate_portfolio_var(positions, confidence=confidence, lookback_days=days)

    if result is None:
        console.print("[bright_yellow]Insufficient price history to calculate VaR "
                      "(need at least 30 trading days).[/bright_yellow]")
        return

    tail_pct = 100 - int(confidence * 100)
    console.print(Panel(
        f"Portfolio value:     [bold bright_white]${result['portfolio_val']:,.2f}[/bold bright_white]\n"
        f"Positions:           [dim]{', '.join(result['tickers'])}[/dim]\n"
        f"History used:        {result['n_days']} trading days\n\n"
        f"{pct_str} 1-Day VaR:   [bold bright_red]-${result['var_1d']:,.2f}[/bold bright_red]  "
        f"([bold bright_red]{result['var_pct']:.2f}%[/bold bright_red] of portfolio)\n"
        f"{pct_str} CVaR (ES):   [bold bright_red]-${result['cvar_1d']:,.2f}[/bold bright_red]  "
        f"[dim](expected loss beyond VaR)[/dim]\n\n"
        f"[dim]On the worst {tail_pct}% of trading days, this portfolio is "
        f"expected to lose more than ${result['var_1d']:,.2f}.[/dim]",
        title=_make_panel_title(f"Portfolio Value at Risk — {pct_str} / 1-Day", "bright_red"),
        border_style="bright_red", box=box.ROUNDED,
    ))


@click.command()
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
        console.print(f"[bright_red]Could not fetch portfolio: {e}[/bright_red]")
        return

    if not positions:
        console.print("[bright_yellow]No open positions found. Buy some stocks first, or use 'buffet-bot whatif' for a hypothetical.[/bright_yellow]")
        return

    total_value = sum(float(p.market_value) for p in positions)
    cash        = float(account.cash)

    # Show current holdings
    tbl = Table(title=f"[bold bright_cyan]Current Holdings[/bold bright_cyan]", box=box.ROUNDED, header_style="bold bright_cyan")
    tbl.add_column("Ticker", style="bold bright_cyan")
    tbl.add_column("Qty",    justify="right", style="bright_white")
    tbl.add_column("Price",  justify="right", style="bright_white")
    tbl.add_column("Value",  justify="right", style="bright_white")
    tbl.add_column("Weight", justify="right", style="bright_white")
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

    console.print("[bold bright_cyan]Querying AI for per-holding return estimates...[/bold bright_cyan]")
    portfolio_est = _get_portfolio_expected_return(positions, primary_model)
    base_return   = portfolio_est['weighted_return']
    volatility    = portfolio_est['weighted_volatility']

    # Per-ticker table
    tbl2 = Table(title=f"[bold bright_yellow]AI Return Estimates[/bold bright_yellow]", box=box.ROUNDED, header_style="bold bright_cyan")
    tbl2.add_column("Ticker", style="bold bright_cyan")
    tbl2.add_column("Weight",    justify="right", style="bright_white")
    tbl2.add_column("Est. Return", justify="right", style="bright_white")
    tbl2.add_column("Est. Vol",    justify="right", style="bright_white")
    tbl2.add_column("Rationale", style="dim bright_white")
    for sym, d in portfolio_est['per_ticker'].items():
        tbl2.add_row(
            sym,
            f"{d['weight']:.1%}",
            f"{d['expected_annual_return_pct']:.1f}%",
            f"{d['volatility_pct']:.1f}%",
            d.get('rationale', '')[:60],
        )
    console.print(tbl2)
    console.print(f"\n[bold bright_cyan]Portfolio weighted return:[/bold bright_cyan] [bright_green]{base_return:.1%}[/bright_green]  "
                  f"[bold bright_cyan]Volatility:[/bold bright_cyan] [bright_yellow]{volatility:.1%}[/bright_yellow]\n")

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


@click.command()
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


@click.command()
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


@click.command()
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


@click.command()
@click.option('--chart/--no-chart', 'show_chart', default=True, show_default=True,
              help='Display plotext horizontal bar chart after the table.')
def sectors(show_chart):
    """Sector allocation breakdown for current portfolio: buffet-bot sectors

    \b
    Fetches GICS sector for every open position via yfinance, then shows
    a weighted table and a plotext bar chart so you can spot concentration risk.
    """
    try:
        positions = trading_client.get_all_positions()
    except Exception as e:
        console.print(f"[bright_red]Could not fetch positions: {e}[/bright_red]")
        return

    if not positions:
        console.print("[bright_yellow]No open positions.[/bright_yellow]")
        return

    total_value = sum(float(p.market_value) for p in positions)
    if total_value <= 0:
        console.print("[bright_red]Portfolio value is zero.[/bright_red]")
        return

    # Fetch sector for every position (best-effort — Unknown if yfinance fails)
    sector_value: dict = {}
    sector_tickers: dict = {}
    with console.status("[dim]Fetching sector info...[/dim]"):
        for pos in positions:
            sym = pos.symbol
            mv  = float(pos.market_value)
            try:
                sector = yf.Ticker(sym).info.get('sector') or 'Unknown'
            except Exception:
                sector = 'Unknown'
            sector_value[sector]   = sector_value.get(sector, 0.0) + mv
            sector_tickers.setdefault(sector, []).append(sym)

    # Sort by value descending
    sorted_sectors = sorted(sector_value.items(), key=lambda x: x[1], reverse=True)

    tbl = Table(
        title="[bold bright_cyan]Portfolio Sector Allocation[/bold bright_cyan]",
        box=box.ROUNDED, header_style="bold bright_cyan",
    )
    tbl.add_column("Sector",   style="bold bright_cyan", no_wrap=True)
    tbl.add_column("Tickers",  style="dim bright_white")
    tbl.add_column("Value",    justify="right", style="bright_white")
    tbl.add_column("Weight",   justify="right", style="bright_white")
    tbl.add_column("Risk",     justify="center", style="bright_white")

    for sector, val in sorted_sectors:
        pct   = val / total_value
        color = "bright_green" if pct < 0.30 else "bright_yellow" if pct < 0.50 else "bright_red"
        risk  = "[bright_green]OK[/bright_green]" if pct < 0.30 else \
                "[bright_yellow]WATCH[/bright_yellow]" if pct < 0.50 else \
                "[bright_red]CONCENTRATED[/bright_red]"
        tickers_str = ", ".join(sector_tickers.get(sector, []))
        tbl.add_row(
            sector,
            tickers_str,
            f"${val:,.2f}",
            f"[{color}]{pct:.1%}[/{color}]",
            risk,
        )

    console.print(Panel(
        f"Total portfolio value: [bold bright_white]${total_value:,.2f}[/bold bright_white]  |  "
        f"Sectors: [bold bright_white]{len(sorted_sectors)}[/bold bright_white]  |  "
        f"Positions: [bold bright_white]{len(positions)}[/bold bright_white]",
        title=_make_panel_title("Sector Overview", "bright_cyan"),
        border_style="bright_cyan", box=box.ROUNDED,
    ))
    console.print(tbl)

    if show_chart and sorted_sectors:
        labels = [s for s, _ in sorted_sectors]
        values = [round(v / total_value * 100, 1) for _, v in sorted_sectors]
        try:
            plt.clf()
            plt.bar(labels, values, orientation='horizontal', color='cyan')
            plt.title("Sector Allocation (%)")
            plt.xlabel("Portfolio Weight (%)")
            plt.show()
        except Exception as e:
            console.print(f"[dim]Chart unavailable: {e}[/dim]")

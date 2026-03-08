"""Backtesting engine — RSI strategy, Sharpe, drawdown, multi-frame signals."""
from __future__ import annotations

import json as _json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from buffet_bot.globals import console


def _compute_rsi(series, period=14):
    """Compute RSI as a full pandas Series."""
    delta = series.diff()
    gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))


def get_multiframe_signals(ticker: str) -> dict:
    """Daily / weekly / monthly technical signals from 1-year price history."""
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


def _run_edge_backtest(
    tickers: list[str],
    period_years: float = 2.0,
    initial_capital: float = 10_000.0,
    min_edge: float = 60.0,
    top_n: int = 5,
    weights_override: dict[str, float] | None = None,
) -> dict | None:
    """Backtest an equal-weight portfolio of top-EDGE_SCORE tickers vs SPY.

    Edge scores are computed once at the current date (using all available
    historical signals filtered to today).  The portfolio holds the top
    ``top_n`` tickers that score >= ``min_edge``, equal-weighted, for the
    full ``period_years`` window.  Daily price data is fetched from yfinance.

    Anti-lookahead note
    -------------------
    Insider, politician, and earnings signals respect ``simulation_date``
    (set to today) and only use data already in the local SQLite DB.
    Buffett (yfinance) and analyst signals use current fundamentals as a
    proxy — this is disclosed in the returned ``disclaimer`` field.

    Returns
    -------
    {
        'selected':       list[dict],   # [{ticker, edge_score, components}, ...]
        'excluded':       list[dict],   # tickers below min_edge
        'equity_curve':   list[float],
        'spy_equity':     list[float],
        'dates':          list[str],
        'metrics':        dict,
        'spy_metrics':    dict,
        'period_years':   float,
        'initial_capital':float,
        'min_edge':       float,
        'disclaimer':     str,
    }
    or None on failure.
    """
    # ── 1. Score all tickers concurrently ────────────────────────────────────
    from buffet_bot.edge import compute_edge_score  # lazy import avoids circular

    score_results: list[dict] = []
    with console.status('[dim]Computing edge scores...[/dim]', spinner='dots'):
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(compute_edge_score, t, weights_override): t
                for t in tickers
            }
            for fut in as_completed(futures):
                try:
                    score_results.append(fut.result())
                except Exception:
                    pass

    if not score_results:
        return None

    score_results.sort(key=lambda r: r['edge_score'], reverse=True)
    selected = [r for r in score_results if r['edge_score'] >= min_edge][:top_n]
    excluded = [r for r in score_results if r['edge_score'] < min_edge or
                score_results.index(r) >= top_n]

    if not selected:
        return None

    selected_tickers = [r['ticker'] for r in selected]

    # ── 2. Download price history ─────────────────────────────────────────────
    period_str = f'{max(1, int(period_years * 365))}d'
    dl_tickers = selected_tickers + ['SPY']
    try:
        raw = yf.download(dl_tickers, period=period_str, auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw['Close']
        else:
            close = raw[['Close']]
        close = close.dropna(how='all')
    except Exception as e:
        console.print(f'[red]Edge backtest download failed: {e}[/red]')
        return None

    if len(close) < 30:
        return None

    # ── 3. Build daily equity curve ───────────────────────────────────────────
    available = [t for t in selected_tickers if t in close.columns]
    if not available:
        return None

    portfolio_close = close[available].ffill().dropna(how='all')
    spy_close = close['SPY'].dropna() if 'SPY' in close.columns else None

    # Align dates across portfolio tickers
    portfolio_close = portfolio_close.dropna(how='any')
    if portfolio_close.empty:
        return None

    n = len(portfolio_close)
    dates = [str(portfolio_close.index[i])[:10] for i in range(n)]

    # Equal-weight: each ticker gets 1/N of capital at day 0
    n_held = len(available)
    alloc = initial_capital / n_held
    initial_prices = portfolio_close.iloc[0]

    # Shares per ticker (fixed at start — pure buy-and-hold equal-weight)
    shares = {t: alloc / float(initial_prices[t]) for t in available}

    equity_curve: list[float] = []
    for i in range(n):
        day_value = sum(shares[t] * float(portfolio_close.iloc[i][t]) for t in available)
        equity_curve.append(day_value)

    # ── 4. SPY equity curve (buy-and-hold) ────────────────────────────────────
    spy_equity: list[float] = []
    if spy_close is not None:
        spy_aligned = spy_close.reindex(portfolio_close.index).ffill().dropna()
        if not spy_aligned.empty:
            spy_shares = initial_capital / float(spy_aligned.iloc[0])
            spy_equity = [spy_shares * float(spy_aligned.iloc[i])
                          for i in range(len(spy_aligned))]

    # ── 5. Metrics ────────────────────────────────────────────────────────────
    def _metrics_from_curve(curve: list[float]) -> dict:
        eq = np.array(curve, dtype=float)
        total_return = (eq[-1] - eq[0]) / eq[0]
        cagr = (eq[-1] / eq[0]) ** (1.0 / max(period_years, 0.01)) - 1
        dr = pd.Series(np.diff(eq) / np.where(eq[:-1] == 0, 1.0, eq[:-1]))
        return {
            'total_return': round(float(total_return), 4),
            'cagr':         round(float(cagr), 4),
            'sharpe':       round(_calculate_sharpe(dr), 3),
            'max_drawdown': round(_calculate_max_drawdown(curve), 4),
            'final_value':  round(float(eq[-1]), 2),
        }

    metrics = _metrics_from_curve(equity_curve)
    spy_metrics = _metrics_from_curve(spy_equity) if spy_equity else {}

    alpha = round(metrics['cagr'] - spy_metrics.get('cagr', 0), 4) if spy_metrics else None
    metrics['alpha'] = alpha
    metrics['n_tickers'] = n_held

    return {
        'selected':        selected,
        'excluded':        excluded,
        'equity_curve':    equity_curve,
        'spy_equity':      spy_equity,
        'dates':           dates,
        'metrics':         metrics,
        'spy_metrics':     spy_metrics,
        'period_years':    period_years,
        'initial_capital': initial_capital,
        'min_edge':        min_edge,
        'disclaimer': (
            'Buffett and analyst signals use current fundamentals as a proxy. '
            'Insider/politician/earnings signals are filtered to historical DB data. '
            'Past performance does not predict future results.'
        ),
    }

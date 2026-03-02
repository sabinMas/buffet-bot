"""Risk management — ATR sizing, portfolio VaR, sell signals, sector diversity."""
import math
from collections import Counter

import numpy as np
import pandas as pd
import yfinance as yf
from rich.table import Table
from rich import box

from buffet_bot.globals import console
from buffet_bot.data import get_realtime_data, get_buffett_metrics, get_tech_indicators


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
    """ATR + beta-adjusted dynamic position sizing."""
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
    """Historical-simulation Value at Risk for the current portfolio."""
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
            close = raw[['Close']].rename(columns={'Close': tickers[0]})

        returns = close.pct_change().dropna()
        if returns.empty or len(returns) < 30:
            return None

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

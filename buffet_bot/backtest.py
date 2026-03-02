"""Backtesting engine — RSI strategy, Sharpe, drawdown, multi-frame signals."""
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

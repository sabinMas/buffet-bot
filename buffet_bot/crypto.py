"""
buffet_bot/crypto.py
Crypto data (Alpaca paper) + Coinbase live order execution.
"""

from __future__ import annotations

import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone

# ── Supported symbols ─────────────────────────────────────────────────────────

CRYPTO_SYMBOLS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD",
    "LTC/USD", "XRP/USD", "AVAX/USD", "LINK/USD",
]

# Alpaca uses "BTC/USD" format; Coinbase uses "BTC-USD"
def _to_coinbase_id(symbol: str) -> str:
    return symbol.replace("/", "-")


def is_crypto_symbol(symbol: str) -> bool:
    """Return True if symbol looks like a crypto pair."""
    s = symbol.upper()
    return (
        s in CRYPTO_SYMBOLS
        or s.replace("-", "/") in CRYPTO_SYMBOLS
        or s.endswith("/USD")
        or s.endswith("-USD")
        or s.endswith("USD") and len(s) > 3
    )


# ── Alpaca crypto data ────────────────────────────────────────────────────────

def get_crypto_bars(symbol: str, days: int = 30) -> pd.DataFrame:
    """
    Fetch daily OHLCV bars for *symbol* from Alpaca crypto data API.
    Returns a DataFrame with columns [open, high, low, close, volume] indexed by date.
    Falls back to an empty DataFrame on error.
    """
    try:
        from alpaca.data.historical import CryptoHistoricalDataClient
        from alpaca.data.requests import CryptoBarsRequest
        from alpaca.data.timeframe import TimeFrame

        api_key    = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        client = CryptoHistoricalDataClient(api_key, secret_key)

        # Alpaca expects "BTC/USD" format
        alpaca_sym = symbol.replace("-", "/").upper()

        start = datetime.now(timezone.utc) - timedelta(days=days + 5)
        req   = CryptoBarsRequest(
            symbol_or_symbols=alpaca_sym,
            timeframe=TimeFrame.Day,
            start=start,
        )
        bars = client.get_crypto_bars(req)
        df   = bars.df

        if df.empty:
            return pd.DataFrame()

        # Flatten MultiIndex if present
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(alpaca_sym, level=0) if alpaca_sym in df.index.get_level_values(0) else df.droplevel(0)

        df.index = pd.to_datetime(df.index)
        df       = df.rename(columns=str.lower).tail(days)
        return df[["open", "high", "low", "close", "volume"]].copy()

    except Exception:
        return pd.DataFrame()


def get_crypto_quote(symbol: str) -> dict:
    """
    Fetch the latest bid/ask quote for *symbol* from Alpaca.
    Returns {bid, ask, mid} or empty dict on error.
    """
    try:
        from alpaca.data.historical import CryptoHistoricalDataClient
        from alpaca.data.requests import CryptoLatestQuoteRequest

        api_key    = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        client     = CryptoHistoricalDataClient(api_key, secret_key)

        alpaca_sym = symbol.replace("-", "/").upper()
        req  = CryptoLatestQuoteRequest(symbol_or_symbols=alpaca_sym)
        resp = client.get_crypto_latest_quote(req)
        q    = resp[alpaca_sym]

        bid = float(q.bid_price)
        ask = float(q.ask_price)
        return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 6)}
    except Exception:
        return {}


def get_crypto_volatility(symbol: str) -> dict:
    """
    Compute 30-day volatility metrics from daily bars.
    Returns {vol_30d_annualized, max_drawdown, daily_std, last_price}.
    """
    df = get_crypto_bars(symbol, days=30)
    if df.empty or "close" not in df.columns:
        return {}

    close = df["close"].astype(float)
    returns = close.pct_change().dropna()

    daily_std  = float(returns.std())
    annualised = daily_std * (365 ** 0.5)

    # Max drawdown
    peaks   = np.maximum.accumulate(close.values)
    denom   = np.where(peaks == 0, 1.0, peaks)
    drawdowns = (peaks - close.values) / denom
    max_dd  = float(drawdowns.max())

    return {
        "daily_std":           round(daily_std * 100, 2),
        "vol_30d_annualized":  round(annualised * 100, 1),
        "max_drawdown":        round(max_dd * 100, 2),
        "last_price":          round(float(close.iloc[-1]), 6),
        "return_30d":          round((float(close.iloc[-1]) / float(close.iloc[0]) - 1) * 100, 2),
    }


# ── Coinbase live orders ──────────────────────────────────────────────────────

def init_coinbase():
    """
    Lazy-init Coinbase Advanced Trade REST client.
    Returns None if credentials are missing or package not installed.
    """
    api_key    = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    if not api_key or not api_secret:
        return None
    try:
        from coinbase.rest import RESTClient  # coinbase-advanced-py
        return RESTClient(api_key=api_key, api_secret=api_secret)
    except ImportError:
        return None
    except Exception:
        return None


def coinbase_market_buy(symbol: str, usd_amount: float) -> dict:
    """
    Place a Coinbase market buy order spending *usd_amount* USD.
    Returns the order dict or raises on error.
    """
    client = init_coinbase()
    if client is None:
        raise RuntimeError("Coinbase client unavailable (check COINBASE_API_KEY / coinbase-advanced-py)")

    product_id = _to_coinbase_id(symbol)
    order = client.market_order_buy(
        client_order_id=_order_id(),
        product_id=product_id,
        quote_size=str(round(usd_amount, 2)),
    )
    return dict(order)


def coinbase_market_sell(symbol: str, crypto_amount: float) -> dict:
    """
    Place a Coinbase market sell order for *crypto_amount* of *symbol*.
    Returns the order dict or raises on error.
    """
    client = init_coinbase()
    if client is None:
        raise RuntimeError("Coinbase client unavailable (check COINBASE_API_KEY / coinbase-advanced-py)")

    product_id = _to_coinbase_id(symbol)
    order = client.market_order_sell(
        client_order_id=_order_id(),
        product_id=product_id,
        base_size=str(crypto_amount),
    )
    return dict(order)


def get_coinbase_balance() -> dict | None:
    """
    Fetch Coinbase account balances.
    Returns {total_usd, accounts: [{currency, balance}]} or None.
    """
    client = init_coinbase()
    if client is None:
        return None
    try:
        accounts = client.get_accounts()
        rows = []
        total_usd = 0.0
        for acct in (accounts.accounts or []):
            bal = float(acct.available_balance.value)
            cur = acct.available_balance.currency
            if bal > 0:
                rows.append({"currency": cur, "balance": bal})
                if cur == "USD":
                    total_usd += bal
        return {"total_usd": round(total_usd, 2), "accounts": rows}
    except Exception:
        return None


def _order_id() -> str:
    """Generate a simple unique client order ID."""
    import uuid
    return f"buffet-bot-{uuid.uuid4().hex[:12]}"

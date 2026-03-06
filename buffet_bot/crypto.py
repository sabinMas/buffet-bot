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


# ── Analysis helper (moved from main.py per ADR-008) ─────────────────────────

def analyze_crypto(symbol: str, dry_run: bool, primary_model: str,
                   console, models: list, model_colors: dict) -> None:
    """Full crypto analysis flow — data fetch, LLM consensus, optional order.

    Accepts console, models, and model_colors from main.py to avoid circular
    imports (per ADR-008).
    """
    import json
    import click
    import ollama

    from rich.panel import Panel

    console.print(Panel(
        f"[bold]{symbol}[/bold]  |  [dim]Crypto asset[/dim]",
        title="Analyzing Crypto", border_style="yellow"))

    with console.status(f"[bold yellow]Fetching crypto data for {symbol}...[/bold yellow]"):
        quote = get_crypto_quote(symbol)
        vol   = get_crypto_volatility(symbol)
        bars  = get_crypto_bars(symbol, days=30)

    if quote:
        console.print(Panel(
            f"Bid: [dim]${quote.get('bid', 0):,.6f}[/dim]  "
            f"Ask: [dim]${quote.get('ask', 0):,.6f}[/dim]  "
            f"Mid: [bold]${quote.get('mid', 0):,.6f}[/bold]",
            title=f"[bold]{symbol}[/bold] Live Quote",
            border_style="yellow",
        ))
    else:
        console.print(f"[dim]Live quote unavailable for {symbol}[/dim]")

    if vol:
        return_color = "green" if vol.get("return_30d", 0) >= 0 else "red"
        console.print(Panel(
            f"30-day Return:       [{return_color}]{vol.get('return_30d', 0):+.1f}%[/{return_color}]\n"
            f"Annualised Vol:      [yellow]{vol.get('vol_30d_annualized', 0):.1f}%[/yellow]\n"
            f"Daily Std Dev:       {vol.get('daily_std', 0):.2f}%\n"
            f"Max Drawdown (30d):  [red]{vol.get('max_drawdown', 0):.1f}%[/red]",
            title="Volatility Metrics",
            border_style="dim",
        ))

    price_context = ""
    if not bars.empty and "close" in bars.columns:
        last5 = bars["close"].tail(5).round(6).tolist()
        price_context = f"Last 5 closes: {last5}"

    prompt = f"""
Crypto Trading AI — {symbol}
Asset Type: Cryptocurrency
{f"Mid Price: ${quote.get('mid', 0):,.6f}" if quote else ""}
{f"30-day Return: {vol.get('return_30d', 0):+.1f}%" if vol else ""}
{f"Annualised Volatility: {vol.get('vol_30d_annualized', 0):.1f}%" if vol else ""}
{f"Max Drawdown (30d): {vol.get('max_drawdown', 0):.1f}%" if vol else ""}
{price_context}

Rules:
- Crypto is highly volatile; size positions at max 2% of portfolio per trade
- Use momentum + volatility context to inform action
- Stop-loss at 8-15% for crypto (wider than equities)

JSON only: {{"action": "BUY|SELL|HOLD", "confidence": 0.75, "usd_amount": 200, "reason": "2 sentences", "stop_pct": 0.10}}
"""

    secondary = models[1] if len(models) > 1 else None
    models_to_query = [primary_model]
    if secondary and primary_model != secondary:
        models_to_query.append(secondary)

    responses = {}
    for model in models_to_query:
        try:
            resp = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2},
            )
            content = resp["message"]["content"].strip()
            start, end = content.find("{"), content.rfind("}") + 1
            if start >= 0 and end > start:
                responses[model] = json.loads(content[start:end])
            else:
                responses[model] = {"error": "No JSON", "raw": content[:200]}
        except json.JSONDecodeError:
            responses[model] = {"error": "Invalid JSON"}
        except Exception as e:
            responses[model] = {"error": str(e)}

    # Inline _print_ai_responses
    for model, resp in responses.items():
        color = model_colors.get(model, "white")
        content = json.dumps(resp, indent=2) if isinstance(resp, dict) else str(resp)
        console.print(Panel(content,
                            title=f"[bold {color}]{model}[/bold {color}]",
                            border_style=color))

    actions = [r.get("action", "HOLD") for r in responses.values()
               if isinstance(r, dict) and "action" in r]
    consensus = max(set(actions), key=actions.count) if actions else "HOLD"
    # Inline _consensus_text
    c_color = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}.get(consensus, "white")
    console.print(f"\nConsensus: [bold {c_color}]{consensus}[/bold {c_color}]")

    if not dry_run and consensus == "BUY":
        best = max(
            (r for r in responses.values() if isinstance(r, dict) and r.get("action") == "BUY"),
            key=lambda x: x.get("confidence", 0),
            default=None,
        )
        if best is None:
            console.print("[yellow]No valid BUY signal.[/yellow]")
            return

        usd_amount = float(best.get("usd_amount", 100))
        cb_key = os.getenv("COINBASE_API_KEY")
        broker = "Coinbase (live)" if cb_key else "Alpaca paper"
        if click.confirm(f"Execute BUY ${usd_amount:.2f} of {symbol} via {broker}?"):
            if cb_key:
                try:
                    result = coinbase_market_buy(symbol, usd_amount)
                    console.print(f"[bold green]Coinbase order submitted:[/bold green] {result}")
                except Exception as e:
                    console.print(f"[red]Coinbase order error: {e}[/red]")
            else:
                try:
                    from alpaca.trading.requests import MarketOrderRequest as _MOR
                    from alpaca.trading.enums import OrderSide as _OS, TimeInForce as _TIF
                    from buffet_bot.live_guard import get_trading_client, confirm_live_execution
                    if not confirm_live_execution(
                        f"BUY ${usd_amount:.2f} of {symbol}", symbol,
                        1, "BUY", estimated_cost=usd_amount,
                    ):
                        return
                    _client = get_trading_client()
                    alpaca_sym = symbol.replace("/", "")
                    order = _MOR(
                        symbol=alpaca_sym,
                        notional=round(usd_amount, 2),
                        side=_OS.BUY,
                        time_in_force=_TIF.GTC,
                    )
                    result = _client.submit_order(order)
                    console.print(f"[bold green]Alpaca crypto order: {result.id}[/bold green]")
                except Exception as e:
                    console.print(f"[red]Alpaca order error: {e}[/red]")

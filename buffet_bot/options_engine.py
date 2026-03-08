"""
buffet_bot/options_engine.py

Options income engine -- ADR-014 (updated ADR-016).

Public API
----------
fetch_options_chain(ticker, min_dte=0, max_dte=90) -> dict | None
find_optimal_covered_call(ticker, current_price, ...) -> dict | None
find_optimal_csp(ticker, current_price, ...) -> dict | None
screen_covered_calls(tickers, ...) -> list[dict]
screen_protective_puts(tickers, ...) -> list[dict]
find_iron_condor(ticker, current_price, ...) -> dict | None
score_wheel_strategy(ticker, ...) -> dict | None
annualized_yield(premium, strike, dte) -> float
estimate_iv(option_price, spot, strike, dte, ...) -> float | None
black_scholes_price(spot, strike, dte, r, sigma, ...) -> dict
compute_greeks(spot, strike, dte, r, sigma, ...) -> dict
get_options_recommendation(ticker, ...) -> dict | None

All functions return data structures; display logic belongs in cmd_intel.py.
All signals normalised to 0-100 where applicable.  No order placement in this module.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from buffet_bot.globals import console, MODELS, LIVE_MODE
from buffet_bot.data import get_realtime_data, get_buffett_metrics, _yf_semaphore
from buffet_bot.risk import _get_atr
from buffet_bot.edge import compute_edge_score


# ============================================================================
# Constants
# ============================================================================

#: Minimum open interest for a contract to be considered liquid.
MIN_OPEN_INTEREST: int = 100

#: Minimum bid to consider a contract tradeable.
MIN_BID: float = 0.05

#: Risk-free rate assumption for Black-Scholes (annualized).  Updated
#: lazily if FRED data is available but a sensible default otherwise.
RISK_FREE_RATE: float = 0.05

#: Standard delta-proxy OTM ranges keyed by approximate target delta.
#: Maps target_delta -> (low_strike_ratio, high_strike_ratio) for calls.
_CALL_DELTA_RANGES: dict[float, tuple[float, float]] = {
    0.40: (1.00, 1.03),
    0.30: (1.03, 1.07),
    0.20: (1.07, 1.12),
    0.10: (1.12, 1.20),
}

#: Same for puts (strike / price ratios below 1.0).
_PUT_DELTA_RANGES: dict[float, tuple[float, float]] = {
    0.40: (0.97, 1.00),
    0.30: (0.93, 0.97),
    0.20: (0.88, 0.93),
    0.10: (0.80, 0.88),
}


# ============================================================================
# Black-Scholes pricing & Greeks (stdlib + numpy only; no py_vollib)
# ============================================================================

def _norm_cdf(x: float) -> float:
    """Standard normal CDF using the error function from math stdlib."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_price(
    spot: float,
    strike: float,
    dte: int,
    r: float = RISK_FREE_RATE,
    sigma: float = 0.30,
    option_type: str = 'call',
) -> dict:
    """Black-Scholes European option price.

    Parameters
    ----------
    spot   : current underlying price
    strike : option strike price
    dte    : days to expiration
    r      : annualized risk-free rate (e.g. 0.05)
    sigma  : annualized implied volatility (e.g. 0.30)
    option_type : 'call' or 'put'

    Returns
    -------
    {'price': float, 'd1': float, 'd2': float, 'T': float}
    """
    T = max(dte / 365.0, 1e-10)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    if option_type == 'call':
        price = spot * _norm_cdf(d1) - strike * math.exp(-r * T) * _norm_cdf(d2)
    else:
        price = strike * math.exp(-r * T) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)

    return {'price': round(price, 4), 'd1': round(d1, 6), 'd2': round(d2, 6), 'T': round(T, 6)}


def compute_greeks(
    spot: float,
    strike: float,
    dte: int,
    r: float = RISK_FREE_RATE,
    sigma: float = 0.30,
    option_type: str = 'call',
) -> dict:
    """Compute Black-Scholes Greeks for a European option.

    Returns
    -------
    {
        'delta': float,   # price sensitivity to underlying
        'gamma': float,   # delta sensitivity to underlying
        'theta': float,   # time decay per calendar day (negative)
        'vega':  float,   # price sensitivity to 1% IV change
        'rho':   float,   # price sensitivity to 1% rate change
    }
    """
    T = max(dte / 365.0, 1e-10)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    pdf_d1 = _norm_pdf(d1)
    cdf_d1 = _norm_cdf(d1)
    cdf_d2 = _norm_cdf(d2)
    exp_rT = math.exp(-r * T)

    gamma = pdf_d1 / (spot * sigma * sqrt_T)
    vega = spot * pdf_d1 * sqrt_T / 100.0  # per 1% IV move

    if option_type == 'call':
        delta = cdf_d1
        theta = (-(spot * pdf_d1 * sigma) / (2 * sqrt_T)
                 - r * strike * exp_rT * cdf_d2) / 365.0
        rho = strike * T * exp_rT * cdf_d2 / 100.0
    else:
        delta = cdf_d1 - 1.0
        theta = (-(spot * pdf_d1 * sigma) / (2 * sqrt_T)
                 + r * strike * exp_rT * _norm_cdf(-d2)) / 365.0
        rho = -strike * T * exp_rT * _norm_cdf(-d2) / 100.0

    return {
        'delta': round(delta, 4),
        'gamma': round(gamma, 6),
        'theta': round(theta, 4),
        'vega':  round(vega, 4),
        'rho':   round(rho, 4),
    }


def estimate_iv(
    option_price: float,
    spot: float,
    strike: float,
    dte: int,
    r: float = RISK_FREE_RATE,
    option_type: str = 'call',
    tol: float = 1e-5,
    max_iter: int = 100,
) -> float | None:
    """Estimate implied volatility via Newton-Raphson on Black-Scholes.

    Returns annualized IV as a float (e.g. 0.35 = 35%), or None if the
    solver fails to converge (degenerate inputs or illiquid pricing).
    """
    if option_price <= 0 or spot <= 0 or strike <= 0 or dte <= 0:
        return None

    T = max(dte / 365.0, 1e-10)
    sqrt_T = math.sqrt(T)
    sigma = 0.30  # initial guess

    for _ in range(max_iter):
        bs = black_scholes_price(spot, strike, dte, r, sigma, option_type)
        diff = bs['price'] - option_price

        # Vega for Newton step (raw, not divided by 100)
        d1 = bs['d1']
        raw_vega = spot * _norm_pdf(d1) * sqrt_T
        if raw_vega < 1e-12:
            return None

        sigma -= diff / raw_vega
        if sigma <= 0:
            sigma = 0.001
        if abs(diff) < tol:
            return round(sigma, 6)

    return None


# ============================================================================
# Options chain fetching
# ============================================================================

def fetch_options_chain(
    ticker: str,
    min_dte: int = 0,
    max_dte: int = 90,
) -> dict | None:
    """Fetch the full options chain for *ticker* via yfinance.

    Filters expirations to those within [min_dte, max_dte] from today.

    Returns
    -------
    {
        'ticker':     str,
        'spot':       float,
        'expiries':   list[str],         # sorted ascending, YYYY-MM-DD
        'chains':     dict[str, dict],   # expiry -> {'calls': DataFrame, 'puts': DataFrame}
    }
    or None on failure.
    """
    ticker = ticker.upper()
    try:
        with _yf_semaphore:
            t = yf.Ticker(ticker)
            all_expiries = list(t.options)
    except Exception as e:
        console.print(f"[yellow]Warning: could not fetch options expiries for {ticker}: {e}[/yellow]")
        return None

    if not all_expiries:
        return None

    today = datetime.utcnow().date()
    filtered: list[str] = []
    for exp_str in all_expiries:
        try:
            exp_date = datetime.strptime(exp_str, '%Y-%m-%d').date()
            dte = (exp_date - today).days
            if min_dte <= dte <= max_dte:
                filtered.append(exp_str)
        except ValueError:
            continue

    if not filtered:
        # Fall back to nearest available expiry if none match the DTE filter
        filtered = [all_expiries[0]]

    # Fetch spot price
    live = get_realtime_data(ticker)
    spot = live.get('price', 0.0)

    chains: dict[str, dict] = {}
    for exp in filtered:
        try:
            with _yf_semaphore:
                chain = t.option_chain(exp)
            chains[exp] = {
                'calls': chain.calls.copy(),
                'puts': chain.puts.copy(),
            }
        except Exception:
            continue

    if not chains:
        return None

    return {
        'ticker': ticker,
        'spot': spot,
        'expiries': sorted(chains.keys()),
        'chains': chains,
    }


def _filter_liquid(df: pd.DataFrame) -> pd.DataFrame:
    """Filter an options DataFrame to liquid contracts only."""
    mask = (
        (df['openInterest'].fillna(0) >= MIN_OPEN_INTEREST)
        & (df['bid'].fillna(0) >= MIN_BID)
    )
    return df[mask].copy()


def _dte_from_expiry(expiry_str: str) -> int:
    """Days to expiration from an expiry date string."""
    exp = datetime.strptime(expiry_str, '%Y-%m-%d').date()
    return (exp - datetime.utcnow().date()).days


# ============================================================================
# Covered call screening
# ============================================================================

def find_optimal_covered_call(
    ticker: str,
    current_price: float,
    target_delta: float = 0.30,
    min_dte: int = 21,
    max_dte: int = 45,
    min_annualized_yield: float = 0.08,
) -> dict | None:
    """Find the best covered call contract near *target_delta* and DTE range.

    Uses strike/price ratio as a delta proxy when yfinance does not provide
    reliable greeks.  Falls back to Black-Scholes delta when IV is available.

    Returns a contract dict or None if no suitable contract found:
    {
        'ticker':     str,
        'expiry':     str,
        'strike':     float,
        'bid':        float,
        'ask':        float,
        'mid':        float,
        'iv':         float,
        'dte':        int,
        'oi':         int,
        'annual_yield': float,
        'delta_est':  float,
        'greeks':     dict | None,
        'strategy':   'COVERED_CALL',
    }
    """
    if current_price <= 0:
        return None

    chain_data = fetch_options_chain(ticker, min_dte=min_dte, max_dte=max_dte)
    if not chain_data:
        return None

    spot = chain_data['spot'] or current_price

    # Determine strike range from delta proxy
    lo_ratio, hi_ratio = _CALL_DELTA_RANGES.get(target_delta, (1.03, 1.07))
    low_strike = spot * lo_ratio
    high_strike = spot * hi_ratio

    best: dict | None = None
    best_score: float = -1.0

    for expiry, sides in chain_data['chains'].items():
        dte = _dte_from_expiry(expiry)
        if dte < min_dte or dte > max_dte:
            continue

        calls = _filter_liquid(sides['calls'])
        if calls.empty:
            continue

        # Filter to strike range
        mask = (calls['strike'] >= low_strike) & (calls['strike'] <= high_strike)
        candidates = calls[mask]
        if candidates.empty:
            continue

        for _, row in candidates.iterrows():
            bid = float(row.get('bid', 0) or 0)
            ask = float(row.get('ask', 0) or 0)
            mid = (bid + ask) / 2.0 if ask > 0 else bid
            strike = float(row['strike'])
            iv = float(row.get('impliedVolatility', 0) or 0)
            oi = int(row.get('openInterest', 0) or 0)

            if mid <= 0 or strike <= 0:
                continue

            ann_yield = annualized_yield(mid, strike, dte)
            if ann_yield < min_annualized_yield:
                continue

            # Compute Greeks if IV is available
            greeks = None
            delta_est = 1.0 - (strike / spot)  # simple proxy
            if iv > 0:
                greeks = compute_greeks(spot, strike, dte, RISK_FREE_RATE, iv, 'call')
                delta_est = greeks['delta']

            # Score: prefer higher yield with delta closest to target
            delta_penalty = abs(delta_est - target_delta) * 10
            score = ann_yield - delta_penalty

            if score > best_score:
                best_score = score
                best = {
                    'ticker': ticker.upper(),
                    'expiry': expiry,
                    'strike': strike,
                    'bid': bid,
                    'ask': ask,
                    'mid': round(mid, 2),
                    'iv': round(iv, 4),
                    'dte': dte,
                    'oi': oi,
                    'annual_yield': round(ann_yield, 4),
                    'delta_est': round(delta_est, 4),
                    'greeks': greeks,
                    'strategy': 'COVERED_CALL',
                }

    return best


def screen_covered_calls(
    tickers: list[str],
    target_delta: float = 0.30,
    min_dte: int = 21,
    max_dte: int = 45,
    min_annualized_yield: float = 0.08,
    max_workers: int = 4,
) -> list[dict]:
    """Screen multiple tickers for optimal covered call opportunities.

    Uses ThreadPoolExecutor for concurrent chain fetching (ADR-010).
    Returns list of contract dicts sorted by annualized yield descending.
    """
    results: list[dict] = []

    def _scan_one(t: str) -> dict | None:
        live = get_realtime_data(t)
        price = live.get('price', 0)
        if not price:
            return None
        return find_optimal_covered_call(
            t, price, target_delta, min_dte, max_dte, min_annualized_yield,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(_scan_one, t.upper()): t for t in tickers}
        for fut in as_completed(future_map):
            try:
                result = fut.result()
                if result:
                    results.append(result)
            except Exception:
                pass

    results.sort(key=lambda r: r.get('annual_yield', 0), reverse=True)
    return results


# ============================================================================
# Cash-secured put screening
# ============================================================================

def find_optimal_csp(
    ticker: str,
    current_price: float,
    target_delta: float = 0.20,
    min_dte: int = 21,
    max_dte: int = 45,
    max_cash_required: float = 50_000.0,
    min_annualized_yield: float = 0.06,
) -> dict | None:
    """Find the best cash-secured put near *target_delta*.

    Filters: bid > MIN_BID, open_interest >= MIN_OPEN_INTEREST,
    cash collateral <= max_cash_required.

    Returns contract dict or None.
    """
    if current_price <= 0:
        return None

    chain_data = fetch_options_chain(ticker, min_dte=min_dte, max_dte=max_dte)
    if not chain_data:
        return None

    spot = chain_data['spot'] or current_price

    lo_ratio, hi_ratio = _PUT_DELTA_RANGES.get(target_delta, (0.88, 0.93))
    low_strike = spot * lo_ratio
    high_strike = spot * hi_ratio

    best: dict | None = None
    best_score: float = -1.0

    for expiry, sides in chain_data['chains'].items():
        dte = _dte_from_expiry(expiry)
        if dte < min_dte or dte > max_dte:
            continue

        puts = _filter_liquid(sides['puts'])
        if puts.empty:
            continue

        mask = (puts['strike'] >= low_strike) & (puts['strike'] <= high_strike)
        candidates = puts[mask]
        if candidates.empty:
            continue

        for _, row in candidates.iterrows():
            bid = float(row.get('bid', 0) or 0)
            ask = float(row.get('ask', 0) or 0)
            mid = (bid + ask) / 2.0 if ask > 0 else bid
            strike = float(row['strike'])
            iv = float(row.get('impliedVolatility', 0) or 0)
            oi = int(row.get('openInterest', 0) or 0)

            if mid <= 0 or strike <= 0:
                continue

            # Cash collateral = strike * 100 (one contract = 100 shares)
            cash_required = strike * 100.0
            if cash_required > max_cash_required:
                continue

            ann_yield = annualized_yield(mid, strike, dte)
            if ann_yield < min_annualized_yield:
                continue

            greeks = None
            delta_est = (strike / spot) - 1.0  # negative for OTM puts
            if iv > 0:
                greeks = compute_greeks(spot, strike, dte, RISK_FREE_RATE, iv, 'put')
                delta_est = greeks['delta']

            delta_penalty = abs(abs(delta_est) - target_delta) * 10
            score = ann_yield - delta_penalty

            if score > best_score:
                best_score = score
                best = {
                    'ticker': ticker.upper(),
                    'expiry': expiry,
                    'strike': strike,
                    'bid': bid,
                    'ask': ask,
                    'mid': round(mid, 2),
                    'iv': round(iv, 4),
                    'dte': dte,
                    'oi': oi,
                    'cash_required': round(cash_required, 2),
                    'annual_yield': round(ann_yield, 4),
                    'delta_est': round(delta_est, 4),
                    'greeks': greeks,
                    'strategy': 'CASH_SECURED_PUT',
                }

    return best


def screen_protective_puts(
    tickers: list[str],
    target_delta: float = 0.30,
    min_dte: int = 21,
    max_dte: int = 60,
    max_cost_pct: float = 0.03,
    max_workers: int = 4,
) -> list[dict]:
    """Screen portfolio positions for protective put hedges.

    Returns list of put contract dicts where annualized cost is within budget.
    Each entry includes the cost as a percentage of the position value.
    """
    results: list[dict] = []

    def _scan_one(t: str) -> dict | None:
        live = get_realtime_data(t)
        price = live.get('price', 0)
        if not price:
            return None

        chain_data = fetch_options_chain(t, min_dte=min_dte, max_dte=max_dte)
        if not chain_data:
            return None

        spot = chain_data['spot'] or price
        lo_ratio, hi_ratio = _PUT_DELTA_RANGES.get(target_delta, (0.93, 0.97))
        low_strike = spot * lo_ratio
        high_strike = spot * hi_ratio

        best: dict | None = None
        best_cost: float = float('inf')

        for expiry, sides in chain_data['chains'].items():
            dte = _dte_from_expiry(expiry)
            if dte < min_dte or dte > max_dte:
                continue

            puts = _filter_liquid(sides['puts'])
            if puts.empty:
                continue

            mask = (puts['strike'] >= low_strike) & (puts['strike'] <= high_strike)
            candidates = puts[mask]

            for _, row in candidates.iterrows():
                ask = float(row.get('ask', 0) or 0)
                if ask <= 0:
                    continue

                strike = float(row['strike'])
                cost_pct = ask / spot  # per-share cost as % of spot
                ann_cost_pct = cost_pct * (365.0 / max(dte, 1))

                if ann_cost_pct > max_cost_pct:
                    continue

                iv = float(row.get('impliedVolatility', 0) or 0)
                oi = int(row.get('openInterest', 0) or 0)
                bid = float(row.get('bid', 0) or 0)

                greeks = None
                if iv > 0:
                    greeks = compute_greeks(spot, strike, dte, RISK_FREE_RATE, iv, 'put')

                if ask < best_cost:
                    best_cost = ask
                    best = {
                        'ticker': t.upper(),
                        'expiry': expiry,
                        'strike': strike,
                        'bid': bid,
                        'ask': ask,
                        'iv': round(iv, 4),
                        'dte': dte,
                        'oi': oi,
                        'cost_pct': round(cost_pct, 4),
                        'ann_cost_pct': round(ann_cost_pct, 4),
                        'greeks': greeks,
                        'protection_pct': round((1.0 - strike / spot) * 100, 2),
                        'strategy': 'PROTECTIVE_PUT',
                    }
        return best

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(_scan_one, t.upper()): t for t in tickers}
        for fut in as_completed(future_map):
            try:
                result = fut.result()
                if result:
                    results.append(result)
            except Exception:
                pass

    results.sort(key=lambda r: r.get('ann_cost_pct', 1.0))
    return results


# ============================================================================
# Iron condor setup
# ============================================================================

def find_iron_condor(
    ticker: str,
    current_price: float,
    min_dte: int = 21,
    max_dte: int = 45,
    wing_width: float = 5.0,
    target_credit: float = 1.00,
) -> dict | None:
    """Find a range-bound iron condor opportunity.

    An iron condor sells an OTM put + OTM call and buys further OTM
    protection on each side.  Best for low-IV, range-bound stocks.

    Structure:
      - Sell put at ~0.20 delta (support)
      - Buy put at sell_put_strike - wing_width (protection)
      - Sell call at ~0.20 delta (resistance)
      - Buy call at sell_call_strike + wing_width (protection)

    Returns dict with all four legs or None.
    """
    if current_price <= 0:
        return None

    chain_data = fetch_options_chain(ticker, min_dte=min_dte, max_dte=max_dte)
    if not chain_data:
        return None

    spot = chain_data['spot'] or current_price

    # Target ~0.20 delta strikes
    put_lo, put_hi = _PUT_DELTA_RANGES[0.20]
    call_lo, call_hi = _CALL_DELTA_RANGES[0.20]

    sell_put_target = spot * (put_lo + put_hi) / 2.0
    sell_call_target = spot * (call_lo + call_hi) / 2.0

    best: dict | None = None
    best_credit: float = -1.0

    for expiry, sides in chain_data['chains'].items():
        dte = _dte_from_expiry(expiry)
        if dte < min_dte or dte > max_dte:
            continue

        calls = _filter_liquid(sides['calls'])
        puts = _filter_liquid(sides['puts'])
        if calls.empty or puts.empty:
            continue

        # Find sell put (closest to target)
        if puts.empty:
            continue
        put_diffs = (puts['strike'] - sell_put_target).abs()
        sell_put_idx = put_diffs.idxmin()
        sell_put = puts.loc[sell_put_idx]
        sell_put_strike = float(sell_put['strike'])

        # Find buy put (wing)
        buy_put_target_strike = sell_put_strike - wing_width
        buy_puts = puts[puts['strike'] <= buy_put_target_strike]
        if buy_puts.empty:
            continue
        buy_put = buy_puts.iloc[-1]  # highest strike <= target
        buy_put_strike = float(buy_put['strike'])

        # Find sell call (closest to target)
        call_diffs = (calls['strike'] - sell_call_target).abs()
        sell_call_idx = call_diffs.idxmin()
        sell_call = calls.loc[sell_call_idx]
        sell_call_strike = float(sell_call['strike'])

        # Find buy call (wing)
        buy_call_target_strike = sell_call_strike + wing_width
        buy_calls = calls[calls['strike'] >= buy_call_target_strike]
        if buy_calls.empty:
            continue
        buy_call = buy_calls.iloc[0]  # lowest strike >= target
        buy_call_strike = float(buy_call['strike'])

        # Calculate net credit
        sell_put_bid = float(sell_put.get('bid', 0) or 0)
        buy_put_ask = float(buy_put.get('ask', 0) or 0)
        sell_call_bid = float(sell_call.get('bid', 0) or 0)
        buy_call_ask = float(buy_call.get('ask', 0) or 0)

        net_credit = (sell_put_bid + sell_call_bid) - (buy_put_ask + buy_call_ask)
        if net_credit < target_credit:
            continue

        max_loss = max(
            sell_put_strike - buy_put_strike,
            buy_call_strike - sell_call_strike,
        ) - net_credit
        max_loss = max(max_loss, 0.01)

        risk_reward = net_credit / max_loss

        if net_credit > best_credit:
            best_credit = net_credit
            best = {
                'ticker': ticker.upper(),
                'expiry': expiry,
                'dte': dte,
                'sell_put': {'strike': sell_put_strike, 'bid': sell_put_bid},
                'buy_put': {'strike': buy_put_strike, 'ask': buy_put_ask},
                'sell_call': {'strike': sell_call_strike, 'bid': sell_call_bid},
                'buy_call': {'strike': buy_call_strike, 'ask': buy_call_ask},
                'net_credit': round(net_credit, 2),
                'max_loss': round(max_loss, 2),
                'risk_reward': round(risk_reward, 4),
                'breakeven_low': round(sell_put_strike - net_credit, 2),
                'breakeven_high': round(sell_call_strike + net_credit, 2),
                'strategy': 'IRON_CONDOR',
            }

    return best


# ============================================================================
# Wheel strategy scoring
# ============================================================================

def score_wheel_strategy(
    ticker: str,
    min_edge_score: float = 65.0,
    min_dte: int = 21,
    max_dte: int = 45,
) -> dict | None:
    """Score a ticker for wheel strategy suitability.

    The wheel: sell cash-secured puts -> if assigned, sell covered calls ->
    repeat.  Good for high-conviction, range-bound to mildly bullish stocks.

    Scoring factors (0-100):
      - Edge score (30%): must exceed min_edge_score
      - Put premium yield (25%): annualized CSP yield
      - Call premium yield (25%): annualized CC yield
      - ATR stability (10%): lower ATR% = more predictable
      - Liquidity (10%): open interest depth

    Returns dict with wheel score and component details, or None.
    """
    live = get_realtime_data(ticker)
    price = live.get('price', 0)
    if not price:
        return None

    # Check edge score
    try:
        edge = compute_edge_score(ticker)
        edge_val = edge.get('edge_score', 0)
    except Exception:
        edge_val = 50.0

    if edge_val < min_edge_score:
        return None

    # Find best CSP and CC
    csp = find_optimal_csp(ticker, price, min_dte=min_dte, max_dte=max_dte,
                           min_annualized_yield=0.0)
    cc = find_optimal_covered_call(ticker, price, min_dte=min_dte, max_dte=max_dte,
                                   min_annualized_yield=0.0)

    put_yield = csp['annual_yield'] if csp else 0.0
    call_yield = cc['annual_yield'] if cc else 0.0

    # ATR stability
    atr = _get_atr(ticker)
    atr_pct = (atr / price * 100) if atr and price else 5.0
    # Lower ATR% = better for wheel; map 0-10% ATR to 100-0 score
    atr_score = max(0.0, min(100.0, 100.0 - atr_pct * 10))

    # Liquidity score from CSP open interest
    oi = csp.get('oi', 0) if csp else 0
    liquidity_score = min(100.0, oi / 10.0)  # 1000 OI = 100

    # Composite
    edge_component = min(100.0, edge_val / min_edge_score * 100)
    put_component = min(100.0, put_yield / 0.20 * 100)   # 20% yield = 100
    call_component = min(100.0, call_yield / 0.20 * 100)

    wheel_score = (
        edge_component * 0.30
        + put_component * 0.25
        + call_component * 0.25
        + atr_score * 0.10
        + liquidity_score * 0.10
    )

    return {
        'ticker': ticker.upper(),
        'wheel_score': round(wheel_score, 1),
        'edge_score': round(edge_val, 1),
        'put_yield': round(put_yield, 4),
        'call_yield': round(call_yield, 4),
        'combined_yield': round(put_yield + call_yield, 4),
        'atr_pct': round(atr_pct, 2),
        'atr_score': round(atr_score, 1),
        'liquidity_score': round(liquidity_score, 1),
        'price': price,
        'csp': csp,
        'cc': cc,
        'strategy': 'WHEEL',
    }


# ============================================================================
# Yield calculation
# ============================================================================

def annualized_yield(premium: float, strike: float, dte: int) -> float:
    """Calculate annualized premium yield.

    Formula: (premium / strike) * (365 / dte)

    Parameters
    ----------
    premium : per-share option premium received
    strike  : option strike price (= capital at risk for CSP or basis for CC)
    dte     : days to expiration

    Returns
    -------
    float  e.g. 0.18 = 18% annualized
    """
    if strike <= 0 or dte <= 0:
        return 0.0
    return (premium / strike) * (365.0 / dte)


# ============================================================================
# IV surface estimation
# ============================================================================

def build_iv_surface(
    ticker: str,
    max_dte: int = 120,
) -> dict | None:
    """Build an implied volatility surface for *ticker*.

    Fetches chains across available expiries and computes IV per strike
    using Newton-Raphson on Black-Scholes.

    Returns
    -------
    {
        'ticker': str,
        'spot': float,
        'surface': [
            {'expiry': str, 'dte': int, 'strike': float, 'iv': float, 'type': 'call'|'put'},
            ...
        ],
        'atm_iv': float | None,      # ATM IV for nearest expiry
        'skew':   float | None,      # 25-delta put IV minus 25-delta call IV
    }
    or None on failure.
    """
    chain_data = fetch_options_chain(ticker, min_dte=7, max_dte=max_dte)
    if not chain_data:
        return None

    spot = chain_data['spot']
    if not spot or spot <= 0:
        return None

    surface: list[dict] = []
    atm_iv: float | None = None
    nearest_dte: int = 999

    for expiry in chain_data['expiries']:
        sides = chain_data['chains'].get(expiry)
        if not sides:
            continue
        dte = _dte_from_expiry(expiry)
        if dte <= 0:
            continue

        for opt_type, df in [('call', sides['calls']), ('put', sides['puts'])]:
            for _, row in df.iterrows():
                mid_price = ((float(row.get('bid', 0) or 0)
                              + float(row.get('ask', 0) or 0)) / 2.0)
                if mid_price <= 0:
                    continue

                strike = float(row['strike'])
                iv = estimate_iv(mid_price, spot, strike, dte,
                                 RISK_FREE_RATE, opt_type)
                if iv is None or iv <= 0 or iv > 5.0:
                    continue

                surface.append({
                    'expiry': expiry,
                    'dte': dte,
                    'strike': strike,
                    'iv': round(iv, 4),
                    'type': opt_type,
                })

                # Track ATM IV for nearest expiry
                if (dte < nearest_dte
                        and abs(strike - spot) / spot < 0.02
                        and opt_type == 'call'):
                    atm_iv = round(iv, 4)
                    nearest_dte = dte

    if not surface:
        return None

    # Compute skew: 25-delta put IV - 25-delta call IV for nearest expiry
    skew: float | None = None
    nearest_entries = [s for s in surface if s['dte'] == nearest_dte]
    put_25d = [s for s in nearest_entries
               if s['type'] == 'put' and 0.88 <= s['strike'] / spot <= 0.93]
    call_25d = [s for s in nearest_entries
                if s['type'] == 'call' and 1.03 <= s['strike'] / spot <= 1.07]
    if put_25d and call_25d:
        avg_put_iv = sum(s['iv'] for s in put_25d) / len(put_25d)
        avg_call_iv = sum(s['iv'] for s in call_25d) / len(call_25d)
        skew = round(avg_put_iv - avg_call_iv, 4)

    return {
        'ticker': ticker.upper(),
        'spot': spot,
        'surface': surface,
        'atm_iv': atm_iv,
        'skew': skew,
    }


# ============================================================================
# LLM-powered options recommendation
# ============================================================================

def get_options_recommendation(
    ticker: str,
    primary_model: str = 'deepseek-r1',
    portfolio_value: float = 100_000.0,
    risk_tolerance: str = 'medium',
) -> dict | None:
    """Get an LLM-powered options strategy recommendation.

    Gathers fundamentals, options data, and edge score, then asks
    the local Ollama model for a strategy recommendation.

    Returns
    -------
    {
        'ticker':     str,
        'strategy':   str,      # COVERED_CALL | CASH_SECURED_PUT | IRON_CONDOR | WHEEL | NONE
        'confidence': float,    # 0.0-1.0
        'reasoning':  str,
        'contract':   dict | None,   # best contract for the recommended strategy
        'model':      str,
    }
    or None on error.
    """
    import json as _json
    import ollama as _ollama

    from buffet_bot.globals import ensure_ollama_running, MODEL_COLORS

    if not ensure_ollama_running():
        return None

    # Gather context data
    live = get_realtime_data(ticker)
    price = live.get('price', 0)
    if not price:
        return None

    buffett = get_buffett_metrics(ticker)

    try:
        edge = compute_edge_score(ticker)
        edge_val = edge.get('edge_score', 50)
    except Exception:
        edge_val = 50

    atr = _get_atr(ticker)
    atr_pct = round((atr / price * 100), 2) if atr and price else 'N/A'

    # Try to get ATM IV
    iv_data = build_iv_surface(ticker, max_dte=60)
    atm_iv = iv_data.get('atm_iv', 'N/A') if iv_data else 'N/A'
    skew = iv_data.get('skew', 'N/A') if iv_data else 'N/A'

    prompt = f"""Options Strategy Advisor for {ticker}
Price: ${price:.2f}  |  Buffett Score: {buffett.get('score', 0)}/100  |  Edge Score: {edge_val}/100
ATR%: {atr_pct}  |  ATM IV: {atm_iv}  |  IV Skew: {skew}
Portfolio Value: ${portfolio_value:,.0f}  |  Risk Tolerance: {risk_tolerance}
ROE: {buffett.get('roe', 'N/A')}%  |  Debt/Eq: {buffett.get('debt_eq', 'N/A')}  |  Beta: {buffett.get('beta', 'N/A')}

Based on these metrics, recommend the BEST options income strategy.
Consider:
- COVERED_CALL: if you own or would buy shares; best for mildly bullish, high IV
- CASH_SECURED_PUT: if you want to buy at a discount; best for bullish, high edge score
- IRON_CONDOR: if range-bound, low volatility expected
- WHEEL: if high conviction, willing to own shares long-term
- NONE: if the stock is too risky or fundamentals are poor for options income

Return ONLY valid JSON:
{{"strategy": "COVERED_CALL|CASH_SECURED_PUT|IRON_CONDOR|WHEEL|NONE",
  "confidence": 0.85,
  "reasoning": "2-3 sentences explaining why this strategy fits"}}
"""

    try:
        color = MODEL_COLORS.get(primary_model, 'white')
        resp = _ollama.chat(
            model=primary_model,
            messages=[{'role': 'user', 'content': prompt}],
            options={'temperature': 0.2},
        )
        content = resp['message']['content'].strip()
        # Extract JSON from response
        start = content.find('{')
        end = content.rfind('}') + 1
        if start < 0 or end <= start:
            return None

        advice = _json.loads(content[start:end])
        strategy = advice.get('strategy', 'NONE').upper()

        # Find the recommended contract
        contract: dict | None = None
        if strategy == 'COVERED_CALL':
            contract = find_optimal_covered_call(ticker, price)
        elif strategy == 'CASH_SECURED_PUT':
            contract = find_optimal_csp(ticker, price)
        elif strategy == 'IRON_CONDOR':
            contract = find_iron_condor(ticker, price)
        elif strategy == 'WHEEL':
            wh = score_wheel_strategy(ticker, min_edge_score=0)
            contract = wh if wh else None

        return {
            'ticker': ticker.upper(),
            'strategy': strategy,
            'confidence': float(advice.get('confidence', 0.0)),
            'reasoning': advice.get('reasoning', ''),
            'contract': contract,
            'model': primary_model,
        }

    except Exception as e:
        console.print(f"[yellow]Warning: options LLM recommendation failed for {ticker}: {e}[/yellow]")
        return None


# ============================================================================
# Roll check (7-DTE flag)
# ============================================================================

def check_rolls_needed(
    positions: list[dict],
    dte_threshold: int = 7,
) -> list[dict]:
    """Flag open options positions approaching expiration.

    Parameters
    ----------
    positions : list of options_positions rows from db.py
                (each must have 'ticker', 'expiry', 'strike', 'strategy', 'status')
    dte_threshold : flag positions with DTE <= this value

    Returns list of dicts with roll recommendations:
    {
        'position':      dict,     # original position row
        'dte_remaining': int,
        'atr':           float | None,
        'new_strike':    float | None,  # suggested roll strike
        'action':        str,           # 'ROLL_UP' | 'ROLL_DOWN' | 'LET_EXPIRE' | 'CLOSE'
    }
    """
    flagged: list[dict] = []
    today = datetime.utcnow().date()

    for pos in positions:
        if pos.get('status', '').upper() != 'OPEN':
            continue

        try:
            exp = datetime.strptime(pos['expiry'], '%Y-%m-%d').date()
            dte = (exp - today).days
        except (ValueError, KeyError):
            continue

        if dte > dte_threshold:
            continue

        ticker = pos.get('ticker', '')
        strategy = pos.get('strategy', '').upper()
        strike = float(pos.get('strike', 0))

        atr = _get_atr(ticker)
        live = get_realtime_data(ticker)
        price = live.get('price', 0)

        # Determine action
        action = 'LET_EXPIRE'
        new_strike: float | None = None

        if price and strike and atr:
            if strategy == 'COVERED_CALL':
                if price > strike:
                    # ITM: roll up and out
                    action = 'ROLL_UP'
                    new_strike = round(price + atr, 2)
                else:
                    # OTM: let expire worthless (keep premium)
                    action = 'LET_EXPIRE'
            elif strategy in ('CASH_SECURED_PUT', 'CASH_PUT'):
                if price < strike:
                    # ITM: at risk of assignment; roll down
                    action = 'ROLL_DOWN'
                    new_strike = round(price - atr, 2)
                else:
                    action = 'LET_EXPIRE'
            else:
                # Unknown strategy or iron condor: recommend close
                action = 'CLOSE'

        flagged.append({
            'position': pos,
            'dte_remaining': dte,
            'atr': round(atr, 4) if atr else None,
            'new_strike': new_strike,
            'action': action,
        })

    return flagged

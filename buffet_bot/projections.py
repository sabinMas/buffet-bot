"""Projection helpers — future value, Monte Carlo, AI return estimates."""
import json

import numpy as np
import ollama
import plotext as plt

from buffet_bot.globals import console


def _calculate_future_value(principal, monthly_contribution, annual_return, years):
    """Monthly-compounding FV formula. annual_return is a decimal (e.g. 0.09)."""
    if years <= 0:
        return float(principal)
    if annual_return == 0:
        return float(principal) + float(monthly_contribution) * years * 12
    r = annual_return / 12
    n = years * 12
    fv_p = float(principal) * (1 + r) ** n
    fv_c = float(monthly_contribution) * (((1 + r) ** n - 1) / r) if monthly_contribution else 0.0
    return fv_p + fv_c


def _get_ai_expected_return(ticker, primary_model):
    """Ask the LLM for a 5-year expected annual return estimate for ticker."""
    prompt = (
        f"Based on {ticker}'s fundamentals, competitive position, and sector trends, "
        "estimate the expected annualized total return over the next 5 years. "
        "Return ONLY valid JSON — no extra text:\n"
        '{"expected_annual_return_pct": 9.5, "volatility_pct": 18.0, "rationale": "brief"}'
    )
    try:
        resp = ollama.chat(
            model=primary_model,
            messages=[{'role': 'user', 'content': prompt}],
            options={'temperature': 0.3},
        )
        content = resp['message']['content'].strip()
        start, end = content.find('{'), content.rfind('}') + 1
        data = json.loads(content[start:end])
        ret = max(-20.0, min(40.0, float(data.get('expected_annual_return_pct', 7.0))))
        vol = max(5.0,  min(60.0, float(data.get('volatility_pct', 20.0))))
        return {'expected_annual_return_pct': ret, 'volatility_pct': vol,
                'rationale': str(data.get('rationale', ''))[:200]}
    except Exception:
        return {'expected_annual_return_pct': 7.0, 'volatility_pct': 20.0,
                'rationale': 'Default conservative estimate'}


def _get_portfolio_expected_return(positions, primary_model):
    """Weighted-average AI return estimate across all current positions."""
    if not positions:
        return {'weighted_return': 0.07, 'weighted_volatility': 0.20, 'per_ticker': {}}
    total_value = sum(float(p.market_value) for p in positions)
    if total_value == 0:
        return {'weighted_return': 0.07, 'weighted_volatility': 0.20, 'per_ticker': {}}
    per_ticker = {}
    for pos in positions:
        weight = float(pos.market_value) / total_value
        with console.status(f"[dim]AI estimating return for {pos.symbol}...[/dim]"):
            est = _get_ai_expected_return(pos.symbol, primary_model)
        per_ticker[pos.symbol] = {'weight': weight, **est}
    weighted_return = sum(
        d['weight'] * d['expected_annual_return_pct'] / 100 for d in per_ticker.values()
    )
    weighted_vol = sum(
        d['weight'] * d['volatility_pct'] / 100 for d in per_ticker.values()
    )
    return {'weighted_return': weighted_return, 'weighted_volatility': weighted_vol,
            'per_ticker': per_ticker}


def _run_monte_carlo(base_return, volatility, balance, monthly, years, n=1000):
    """Simulate n portfolio paths. Returns percentile dict (all dollar values)."""
    months = years * 12
    monthly_ret = base_return / 12
    monthly_vol = volatility / (12 ** 0.5)
    rng = np.random.default_rng()
    shocks = rng.normal(loc=monthly_ret, scale=monthly_vol, size=(n, months))
    portfolio = np.full(n, float(balance))
    for m in range(months):
        portfolio = portfolio * (1 + shocks[:, m]) + float(monthly)
    portfolio = np.maximum(portfolio, 0)
    return {
        'p10':    float(np.percentile(portfolio, 10)),
        'p25':    float(np.percentile(portfolio, 25)),
        'median': float(np.median(portfolio)),
        'p75':    float(np.percentile(portfolio, 75)),
        'p90':    float(np.percentile(portfolio, 90)),
        'mean':   float(np.mean(portfolio)),
        'std':    float(np.std(portfolio)),
        'n':      n,
    }


def _display_mc_chart(checkpoints, label="Monte Carlo Projection"):
    """Plot p10 / median / p90 lines with plotext."""
    years_x  = [y for y, _ in checkpoints]
    p10_y    = [mc['p10']    for _, mc in checkpoints]
    median_y = [mc['median'] for _, mc in checkpoints]
    p90_y    = [mc['p90']    for _, mc in checkpoints]
    try:
        plt.clf()
        plt.plot(years_x, p90_y,    color='green', label='90th pct')
        plt.plot(years_x, median_y, color='cyan',  label='Median')
        plt.plot(years_x, p10_y,    color='red',   label='10th pct')
        plt.title(label)
        plt.xlabel("Years")
        plt.ylabel("Value ($)")
        plt.show()
    except Exception as e:
        console.print(f"[dim]Chart unavailable: {e}[/dim]")


def _years_to_reach(target, balance, monthly, annual_return, max_years=100):
    """Binary search: how many years until FV >= target? Returns None if unreachable."""
    if balance >= target:
        return 0.0
    if _calculate_future_value(balance, monthly, annual_return, max_years) < target:
        return None
    lo, hi = 0.0, float(max_years)
    for _ in range(60):
        mid = (lo + hi) / 2
        if _calculate_future_value(balance, monthly, annual_return, mid) < target:
            lo = mid
        else:
            hi = mid
    return hi

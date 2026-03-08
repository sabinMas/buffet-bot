"""
buffet_bot/edge.py

Multi-factor edge score — ADR-013.

Public API
----------
compute_edge_score(ticker, weights=None, simulation_date=None, llm_score=50.0) -> dict
compute_insider_signal(ticker, lookback_days=90, simulation_date=None) -> float
compute_politician_signal(ticker, lookback_days=180, simulation_date=None) -> float
compute_earnings_signal(ticker, lookback_quarters=4) -> float

All signals are normalised to 0–100.  50 = neutral / no data.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from buffet_bot.data import get_buffett_metrics, get_analyst_consensus
from buffet_bot.insiders import fetch_insider_transactions, insider_summary
from buffet_bot.politicians import fetch_house_trades, fetch_fmp_trades, merge_deduplicate
from buffet_bot.db import get_earnings_history

# ── Default weights (must sum to 1.0) ─────────────────────────────────────────

DEFAULT_WEIGHTS: dict[str, float] = {
    'buffett':    0.30,
    'llm':        0.20,
    'insider':    0.20,
    'politician': 0.10,
    'earnings':   0.10,
    'analyst':    0.10,
}

_COMPONENT_KEYS = tuple(DEFAULT_WEIGHTS.keys())


# ── Signal helpers ─────────────────────────────────────────────────────────────


def compute_insider_signal(
    ticker: str,
    lookback_days: int = 90,
    simulation_date: datetime | None = None,
) -> float:
    """0–100 score from net insider buy/sell activity.

    50 = neutral (no data or balanced).
    >50 = net buying; <50 = net selling.
    Magnitude scales with net dollar value (caps at $1 M for full swing).
    """
    transactions = fetch_insider_transactions(ticker, days=lookback_days)
    if simulation_date:
        cutoff = simulation_date.strftime('%Y-%m-%d')
        transactions = [t for t in transactions if (t.get('date') or '') <= cutoff]
    if not transactions:
        return 50.0

    summary = insider_summary(transactions)
    sentiment = summary.get('net_sentiment', 'neutral')
    net_value = abs(summary.get('net_value', 0) or 0)
    scale = min(1.0, net_value / 1_000_000)

    if sentiment == 'bullish':
        return round(50.0 + 50.0 * scale, 1)
    if sentiment == 'bearish':
        return round(50.0 - 50.0 * scale, 1)
    return 50.0


def compute_politician_signal(
    ticker: str,
    lookback_days: int = 180,
    simulation_date: datetime | None = None,
) -> float:
    """0–100 score from congressional purchase-vs-sale ratio.

    50 = neutral; 100 = all purchases; 0 = all sales.
    """
    house = fetch_house_trades(ticker, days=lookback_days)
    fmp   = fetch_fmp_trades(ticker)
    trades = merge_deduplicate(house, fmp)
    if simulation_date:
        cutoff = simulation_date.strftime('%Y-%m-%d')
        trades = [t for t in trades if (t.get('date') or '') <= cutoff]
    if not trades:
        return 50.0

    buys  = sum(1 for t in trades if t['action'] == 'Purchase')
    sells = sum(1 for t in trades if t['action'] == 'Sale')
    total = buys + sells
    if total == 0:
        return 50.0
    return round(buys / total * 100, 1)


def compute_earnings_signal(ticker: str, lookback_quarters: int = 4,
                            simulation_date=None) -> float:
    """0–100 score from earnings beat rate over recent quarters.

    100 = beat every quarter; 0 = missed every quarter; 50 = no data.
    simulation_date: if set, excludes earnings reported on/after this date
                     (anti-lookahead bias for backtesting).
    """
    before = simulation_date.strftime('%Y-%m-%d') if simulation_date else None
    rows = get_earnings_history(ticker, limit=lookback_quarters, before_date=before)
    if not rows:
        return 50.0
    beats = sum(1 for r in rows if r.get('beat_miss') == 'BEAT')
    return round(beats / len(rows) * 100, 1)


def _analyst_to_score(consensus: dict) -> float:
    """Map analyst upside_pct to a 0–100 score.

    0% upside → 50; +50% → 100; -50% → 0 (clamped).
    Returns 50.0 when consensus data is unavailable.
    """
    if not consensus:
        return 50.0
    upside = consensus.get('upside_pct')
    if upside is None:
        return 50.0
    return max(0.0, min(100.0, 50.0 + float(upside)))


# ── Main composite scorer ──────────────────────────────────────────────────────


def compute_edge_score(
    ticker: str,
    weights: dict[str, float] | None = None,
    simulation_date: datetime | None = None,
    llm_score: float = 50.0,
) -> dict:
    """Compute a weighted multi-factor edge score for *ticker*.

    Parameters
    ----------
    ticker          : stock symbol (case-insensitive)
    weights         : override any subset of DEFAULT_WEIGHTS; values are
                      re-normalised to sum to 1.0 automatically
    simulation_date : if set, all time-series signals exclude data after
                      this date (anti-lookahead bias for backtesting)
    llm_score       : pre-computed LLM conviction score (0–100); callers may
                      inject the result of an Ollama query here; default 50
                      (neutral) avoids mandatory Ollama calls during bulk scans

    Returns
    -------
    {
        'ticker':          str,
        'edge_score':      float,   # 0-100 weighted composite
        'components':      dict,    # per-factor scores (0-100 each)
        'weights_used':    dict,    # normalised weights actually applied
        'simulation_date': str | None,
    }
    """
    # Build and normalise weights
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in DEFAULT_WEIGHTS})
    total_w = sum(w.values())
    if total_w > 0:
        w = {k: v / total_w for k, v in w.items()}

    components: dict[str, float] = {'llm': round(float(llm_score), 1)}

    # Fan-out concurrent I/O calls
    def _buffett():
        m = get_buffett_metrics(ticker)
        return 'buffett', float(m.get('score', 0))

    def _insider():
        return 'insider', compute_insider_signal(ticker, simulation_date=simulation_date)

    def _politician():
        return 'politician', compute_politician_signal(ticker, simulation_date=simulation_date)

    def _earnings():
        return 'earnings', compute_earnings_signal(ticker, simulation_date=simulation_date)

    def _analyst():
        return 'analyst', _analyst_to_score(get_analyst_consensus(ticker))

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(fn) for fn in (_buffett, _insider, _politician, _earnings, _analyst)]
        for fut in as_completed(futures):
            try:
                key, val = fut.result()
                components[key] = round(val, 1)
            except Exception:
                pass

    # Fill neutral defaults for any component that failed
    for k in _COMPONENT_KEYS:
        components.setdefault(k, 50.0)

    edge_score = sum(components.get(k, 50.0) * w.get(k, 0.0) for k in _COMPONENT_KEYS)

    return {
        'ticker':          ticker.upper(),
        'edge_score':      round(edge_score, 1),
        'components':      {k: components[k] for k in _COMPONENT_KEYS},
        'weights_used':    {k: round(v, 4) for k, v in w.items()},
        'simulation_date': simulation_date.isoformat() if simulation_date else None,
    }

"""
buffet_bot/macro.py

Macro intelligence engine -- ADR-015 design, ADR-017 implementation.

Public API
----------
get_macro_regime() -> dict
get_sector_rotation_signal(sector: str) -> dict
compute_macro_score(ticker: str, sector: str | None = None) -> float
get_recession_probability() -> float
macro_prompt_block(ticker: str) -> str

All functions return data structures (dicts / floats / strings).
No Rich output, no order placement, no CLI concerns.
Neutral fallback (50.0 / 0.5 / empty string) on any failure -- never raises.
Thread-safe -- may be called from ThreadPoolExecutor.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from buffet_bot.data import _fetch_fred_data, _yf_semaphore

# Try to import universe for sector lookups; degrade gracefully
try:
    from buffet_bot.universe import _COMPANY_DB as _UNIVERSE_DB
except ImportError:
    _UNIVERSE_DB: dict = {}


# ============================================================================
# Constants
# ============================================================================

#: Macro regime labels.
REGIMES = ("RISK_ON", "RISK_OFF", "STAGFLATION", "NEUTRAL")

#: FRED series already fetched by _fetch_fred_data():
#:   DFF  -> fed_rate     (daily effective federal funds rate, %)
#:   T10Y2Y -> yield_curve (10Y minus 2Y treasury spread, %)
#:   CPIAUCSL -> cpi      (consumer price index level, not YoY %)
#:
#: Note: CPIAUCSL is the index *level* (e.g. 314.5), not YoY inflation.
#: We approximate YoY inflation as: (latest - latest * 100/104) -- but since
#: we only get the *latest* observation from FRED, we use the level as a proxy
#: by checking if it is above historical thresholds.  For the regime classifier,
#: we interpret cpi > 300 as the modern baseline (Jan 2023 ~ 300) and use the
#: fed_rate as the stronger inflation signal since it directly reflects the
#: Fed's response to inflation.

#: Sector rotation mapping: regime -> {sector: signal}
#: Signals: "OVERWEIGHT", "UNDERWEIGHT", "NEUTRAL"
_SECTOR_ROTATION: dict[str, dict[str, str]] = {
    "RISK_ON": {
        "Technology":             "OVERWEIGHT",
        "Consumer Discretionary": "OVERWEIGHT",
        "Communication Services": "OVERWEIGHT",
        "Financials":             "OVERWEIGHT",
        "Industrials":            "NEUTRAL",
        "Healthcare":             "NEUTRAL",
        "Materials":              "NEUTRAL",
        "Real Estate":            "NEUTRAL",
        "Energy":                 "UNDERWEIGHT",
        "Consumer Staples":       "UNDERWEIGHT",
        "Utilities":              "UNDERWEIGHT",
    },
    "RISK_OFF": {
        "Utilities":              "OVERWEIGHT",
        "Healthcare":             "OVERWEIGHT",
        "Consumer Staples":       "OVERWEIGHT",
        "Real Estate":            "NEUTRAL",
        "Communication Services": "NEUTRAL",
        "Industrials":            "NEUTRAL",
        "Materials":              "UNDERWEIGHT",
        "Energy":                 "UNDERWEIGHT",
        "Technology":             "UNDERWEIGHT",
        "Consumer Discretionary": "UNDERWEIGHT",
        "Financials":             "UNDERWEIGHT",
    },
    "STAGFLATION": {
        "Energy":                 "OVERWEIGHT",
        "Materials":              "OVERWEIGHT",
        "Utilities":              "OVERWEIGHT",
        "Healthcare":             "NEUTRAL",
        "Consumer Staples":       "NEUTRAL",
        "Industrials":            "NEUTRAL",
        "Real Estate":            "UNDERWEIGHT",
        "Communication Services": "UNDERWEIGHT",
        "Financials":             "UNDERWEIGHT",
        "Technology":             "UNDERWEIGHT",
        "Consumer Discretionary": "UNDERWEIGHT",
    },
    "NEUTRAL": {
        "Technology":             "NEUTRAL",
        "Consumer Discretionary": "NEUTRAL",
        "Communication Services": "NEUTRAL",
        "Financials":             "NEUTRAL",
        "Industrials":            "NEUTRAL",
        "Healthcare":             "NEUTRAL",
        "Materials":              "NEUTRAL",
        "Real Estate":            "NEUTRAL",
        "Energy":                 "NEUTRAL",
        "Consumer Staples":       "NEUTRAL",
        "Utilities":              "NEUTRAL",
    },
}

#: Sector inflation sensitivity: how much a sector is hurt by high inflation.
#: Higher values = more negatively sensitive to inflation.
#: Used in compute_macro_score() for the inflation factor.
_INFLATION_SENSITIVITY: dict[str, float] = {
    "Technology":             0.7,
    "Consumer Discretionary": 0.8,
    "Communication Services": 0.5,
    "Financials":             0.4,
    "Industrials":            0.5,
    "Healthcare":             0.3,
    "Materials":              0.2,   # commodity producers benefit
    "Real Estate":            0.6,
    "Energy":                 0.1,   # energy producers benefit from inflation
    "Consumer Staples":       0.3,   # pricing power
    "Utilities":              0.4,   # regulated, mixed
}

#: Signal score mapping for compute_macro_score regime alignment factor.
_SIGNAL_SCORES: dict[str, float] = {
    "OVERWEIGHT":  80.0,
    "NEUTRAL":     50.0,
    "UNDERWEIGHT": 20.0,
}

#: Simple in-memory cache for the macro regime to avoid redundant FRED calls
#: within a single process lifetime.  Tuple of (timestamp, result_dict).
_regime_cache: dict[str, object] = {"ts": 0.0, "data": None}
_regime_cache_lock = threading.Lock()
_REGIME_CACHE_TTL = 300  # 5 minutes


# ============================================================================
# Internal helpers
# ============================================================================


def _get_cached_regime() -> dict | None:
    """Return cached regime if still valid, else None."""
    with _regime_cache_lock:
        ts = _regime_cache.get("ts", 0.0)
        if time.time() - ts < _REGIME_CACHE_TTL and _regime_cache.get("data"):
            return _regime_cache["data"]  # type: ignore[return-value]
    return None


def _set_cached_regime(data: dict) -> None:
    """Store regime result in the in-memory cache."""
    with _regime_cache_lock:
        _regime_cache["ts"] = time.time()
        _regime_cache["data"] = data


def _classify_regime(
    fed_rate: float | None,
    yield_curve: float | None,
    cpi: float | None,
) -> tuple[str, float, dict]:
    """Deterministic regime classifier from FRED indicators.

    Parameters
    ----------
    fed_rate     : effective federal funds rate (%)
    yield_curve  : 10Y-2Y treasury spread (%)
    cpi          : CPI index level (used as proxy; fed_rate is the primary
                   inflation signal since the Fed responds to inflation)

    Returns
    -------
    (regime, confidence, signals_dict)

    Classification rules (evaluated in priority order):
    1. STAGFLATION: fed_rate > 5 AND yield_curve < 0
       -- restrictive policy + inverted curve = worst-case macro
    2. RISK_OFF: yield_curve < 0 OR fed_rate > 5
       -- either inversion or restrictive policy alone = defensive
    3. RISK_ON: yield_curve > 0.5 AND fed_rate <= 3.5
       -- positive spread + accommodative policy = favorable
    4. NEUTRAL: everything else
    """
    signals: dict[str, str] = {}

    # Default to None-safe values
    fr = fed_rate if fed_rate is not None else 3.0   # assume moderate if unknown
    yc = yield_curve if yield_curve is not None else 0.5  # assume normal if unknown

    # Build signal descriptions
    if yield_curve is not None:
        if yc < 0:
            signals["yield_curve"] = f"INVERTED ({yc:+.2f}%)"
        elif yc < 0.5:
            signals["yield_curve"] = f"FLAT ({yc:+.2f}%)"
        else:
            signals["yield_curve"] = f"NORMAL ({yc:+.2f}%)"
    else:
        signals["yield_curve"] = "UNAVAILABLE"

    if fed_rate is not None:
        if fr > 5:
            signals["fed_rate"] = f"RESTRICTIVE ({fr:.2f}%)"
        elif fr > 3.5:
            signals["fed_rate"] = f"ELEVATED ({fr:.2f}%)"
        elif fr > 1.5:
            signals["fed_rate"] = f"MODERATE ({fr:.2f}%)"
        else:
            signals["fed_rate"] = f"ACCOMMODATIVE ({fr:.2f}%)"
    else:
        signals["fed_rate"] = "UNAVAILABLE"

    if cpi is not None:
        signals["cpi_level"] = f"{cpi:.1f}"
    else:
        signals["cpi_level"] = "UNAVAILABLE"

    # Count how many indicators we actually have
    available = sum(1 for v in [fed_rate, yield_curve] if v is not None)

    # Classification logic
    # STAGFLATION: restrictive monetary policy AND inverted yield curve
    if fr > 5 and yc < 0:
        confidence = 0.8 if available == 2 else 0.5
        # Boost confidence if deeply inverted
        if yc < -0.5:
            confidence = min(1.0, confidence + 0.1)
        return "STAGFLATION", round(confidence, 2), signals

    # RISK_OFF: either inverted curve OR restrictive policy
    if yc < 0 or fr > 5:
        confidence = 0.7 if available >= 2 else 0.4
        # Stronger signal if both are concerning (but not quite STAGFLATION)
        if yc < 0 and fr > 3.5:
            confidence = min(1.0, confidence + 0.1)
        return "RISK_OFF", round(confidence, 2), signals

    # RISK_ON: positive spread AND accommodative/moderate policy
    if yc > 0.5 and fr <= 3.5:
        confidence = 0.7 if available >= 2 else 0.4
        # Stronger signal if very accommodative
        if fr <= 1.5 and yc > 1.0:
            confidence = min(1.0, confidence + 0.15)
        return "RISK_ON", round(confidence, 2), signals

    # NEUTRAL: mixed signals or moderate values
    confidence = 0.5 if available >= 2 else 0.3
    return "NEUTRAL", round(confidence, 2), signals


def _lookup_sector(ticker: str) -> str | None:
    """Look up sector for a ticker from the bundled universe DB.

    Falls back to yfinance if not in the bundled DB.
    Returns None if sector cannot be determined.
    """
    # Fast path: check bundled universe
    entry = _UNIVERSE_DB.get(ticker.upper())
    if entry:
        return entry.get("sector")

    # Slow path: yfinance lookup (respects semaphore)
    try:
        import yfinance as yf
        with _yf_semaphore:
            info = yf.Ticker(ticker).info
        return info.get("sector")
    except Exception:
        return None


# ============================================================================
# Public API
# ============================================================================


def get_macro_regime() -> dict:
    """Classify the current macroeconomic environment using FRED data.

    Returns
    -------
    dict with keys:
        regime     : str   -- one of RISK_ON, RISK_OFF, STAGFLATION, NEUTRAL
        confidence : float -- 0.0 to 1.0
        signals    : dict  -- human-readable signal descriptions
        cached     : bool  -- True if result came from in-memory cache

    On failure (no FRED API key, network error), returns a NEUTRAL regime
    with low confidence rather than raising.
    """
    # Check in-memory cache first
    cached = _get_cached_regime()
    if cached is not None:
        result = dict(cached)
        result["cached"] = True
        return result

    try:
        fred = _fetch_fred_data()
    except Exception:
        fred = {}

    if not fred:
        return {
            "regime": "NEUTRAL",
            "confidence": 0.2,
            "signals": {"note": "FRED data unavailable (check FRED_API_KEY)"},
            "cached": False,
        }

    fed_rate = fred.get("fed_rate")
    yield_curve = fred.get("yield_curve")
    cpi = fred.get("cpi")

    regime, confidence, signals = _classify_regime(fed_rate, yield_curve, cpi)

    result = {
        "regime": regime,
        "confidence": confidence,
        "signals": signals,
        "cached": False,
    }

    _set_cached_regime(result)
    return result


def get_sector_rotation_signal(sector: str) -> dict:
    """Return the sector rotation signal for a given sector under the current regime.

    Parameters
    ----------
    sector : str -- GICS sector name (e.g. "Technology", "Healthcare")

    Returns
    -------
    dict with keys:
        signal : str -- OVERWEIGHT, UNDERWEIGHT, or NEUTRAL
        reason : str -- human-readable explanation
        regime : str -- the current macro regime used for the determination

    Returns NEUTRAL signal on any failure.
    """
    try:
        regime_data = get_macro_regime()
    except Exception:
        return {
            "signal": "NEUTRAL",
            "reason": "Macro regime unavailable",
            "regime": "NEUTRAL",
        }

    regime = regime_data.get("regime", "NEUTRAL")
    rotation = _SECTOR_ROTATION.get(regime, _SECTOR_ROTATION["NEUTRAL"])

    # Normalize sector name for case-insensitive matching
    sector_normalized = None
    for known_sector in rotation:
        if known_sector.lower() == sector.lower():
            sector_normalized = known_sector
            break

    if sector_normalized is None:
        return {
            "signal": "NEUTRAL",
            "reason": f"Unknown sector '{sector}' -- defaulting to NEUTRAL",
            "regime": regime,
        }

    signal = rotation.get(sector_normalized, "NEUTRAL")

    # Build reason string
    reasons = {
        ("RISK_ON", "OVERWEIGHT"): f"{sector_normalized} benefits from economic expansion and risk appetite",
        ("RISK_ON", "UNDERWEIGHT"): f"{sector_normalized} underperforms when growth/cyclical sectors lead",
        ("RISK_OFF", "OVERWEIGHT"): f"{sector_normalized} is defensive and outperforms in risk-off environments",
        ("RISK_OFF", "UNDERWEIGHT"): f"{sector_normalized} is cyclical and vulnerable in risk-off regimes",
        ("STAGFLATION", "OVERWEIGHT"): f"{sector_normalized} has pricing power or benefits from commodity inflation",
        ("STAGFLATION", "UNDERWEIGHT"): f"{sector_normalized} is hurt by rising costs and slowing demand",
        ("NEUTRAL", "NEUTRAL"): f"Mixed macro signals -- no strong directional bias for {sector_normalized}",
    }
    reason = reasons.get(
        (regime, signal),
        f"{sector_normalized} is {signal.lower()} in a {regime} regime",
    )

    return {
        "signal": signal,
        "reason": reason,
        "regime": regime,
    }


def compute_macro_score(ticker: str, sector: str | None = None) -> float:
    """Compute a 0-100 macro tailwind score for a given ticker.

    Factors (weighted):
        - Regime alignment with sector  (40%) -- how favorable is the current
          macro regime for this ticker's sector?
        - Yield curve signal            (30%) -- positive spread = tailwind,
          inverted = headwind
        - Inflation sensitivity         (30%) -- sectors more hurt by inflation
          get penalized when rates are high

    Parameters
    ----------
    ticker : str            -- stock symbol
    sector : str | None     -- GICS sector; if None, looked up automatically

    Returns
    -------
    float -- 0.0 to 100.0; returns 50.0 (neutral) on any failure
    """
    try:
        # Resolve sector
        if sector is None:
            sector = _lookup_sector(ticker)
        if sector is None:
            return 50.0

        # Get macro regime
        regime_data = get_macro_regime()
        regime = regime_data.get("regime", "NEUTRAL")
        signals = regime_data.get("signals", {})

        # ── Factor 1: Regime alignment (40%) ────────────────────────────
        rotation = _SECTOR_ROTATION.get(regime, _SECTOR_ROTATION["NEUTRAL"])
        # Case-insensitive sector match
        signal_str = "NEUTRAL"
        for known_sector, sig in rotation.items():
            if known_sector.lower() == sector.lower():
                signal_str = sig
                break
        alignment_score = _SIGNAL_SCORES.get(signal_str, 50.0)

        # ── Factor 2: Yield curve signal (30%) ──────────────────────────
        # Parse yield curve from signals dict or re-fetch
        yc_score = 50.0  # neutral default
        yc_str = signals.get("yield_curve", "UNAVAILABLE")
        if "INVERTED" in yc_str:
            yc_score = 15.0  # strongly negative
        elif "FLAT" in yc_str:
            yc_score = 35.0  # mildly negative
        elif "NORMAL" in yc_str:
            yc_score = 75.0  # positive

        # ── Factor 3: Inflation sensitivity (30%) ───────────────────────
        # When rates are high (restrictive), inflation-sensitive sectors are
        # penalized.  When rates are low, they get a tailwind.
        sensitivity = _INFLATION_SENSITIVITY.get(sector, 0.5)
        inflation_score = 50.0  # neutral default
        fr_str = signals.get("fed_rate", "UNAVAILABLE")
        if "RESTRICTIVE" in fr_str:
            # High rates: penalize inflation-sensitive sectors more
            inflation_score = 50.0 - (sensitivity * 40.0)
        elif "ELEVATED" in fr_str:
            inflation_score = 50.0 - (sensitivity * 20.0)
        elif "MODERATE" in fr_str:
            inflation_score = 50.0 + (sensitivity * 10.0)
        elif "ACCOMMODATIVE" in fr_str:
            # Low rates: benefit inflation-sensitive (growth) sectors
            inflation_score = 50.0 + (sensitivity * 30.0)

        # Clamp inflation_score to 0-100
        inflation_score = max(0.0, min(100.0, inflation_score))

        # ── Weighted composite ──────────────────────────────────────────
        score = (
            alignment_score * 0.40
            + yc_score * 0.30
            + inflation_score * 0.30
        )
        return round(max(0.0, min(100.0, score)), 1)

    except Exception:
        return 50.0


def get_recession_probability() -> float:
    """Estimate recession probability from yield curve and macro signals.

    Primary signal: 10Y-2Y yield curve (T10Y2Y) inversion.
    - Inverted (< 0): elevated probability, scaled by magnitude
    - Flat (0 to 0.5): moderate probability
    - Normal (> 0.5): lower probability

    Secondary signal: fed funds rate level.
    - Restrictive (> 5%): contributes to recession risk

    Returns
    -------
    float -- 0.0 to 1.0 probability estimate; returns 0.5 on data unavailability
    """
    try:
        fred = {}
        regime_data = get_macro_regime()
        signals = regime_data.get("signals", {})

        # Parse yield curve from signals
        yc_str = signals.get("yield_curve", "UNAVAILABLE")
        fr_str = signals.get("fed_rate", "UNAVAILABLE")

        if yc_str == "UNAVAILABLE" and fr_str == "UNAVAILABLE":
            return 0.5  # no data

        # Base probability from yield curve
        base_prob = 0.25  # baseline
        if "INVERTED" in yc_str:
            # Extract the numeric value if possible
            try:
                # Format is "INVERTED (-0.50%)" or similar
                val_str = yc_str.split("(")[1].rstrip("%)")
                yc_val = float(val_str)
                # Deeper inversion = higher probability
                # -0.5% -> ~60%, -1.0% -> ~75%, -2.0% -> ~85%
                base_prob = min(0.90, 0.50 + abs(yc_val) * 0.25)
            except (IndexError, ValueError):
                base_prob = 0.60  # inverted but can't parse magnitude
        elif "FLAT" in yc_str:
            base_prob = 0.35
        elif "NORMAL" in yc_str:
            base_prob = 0.15

        # Adjust for fed funds rate
        rate_adj = 0.0
        if "RESTRICTIVE" in fr_str:
            rate_adj = 0.10
        elif "ELEVATED" in fr_str:
            rate_adj = 0.05
        elif "ACCOMMODATIVE" in fr_str:
            rate_adj = -0.10

        probability = max(0.0, min(1.0, base_prob + rate_adj))
        return round(probability, 2)

    except Exception:
        return 0.5


def macro_prompt_block(ticker: str) -> str:
    """Build a compact string for injection into LLM analysis prompts.

    Summarizes the current macro regime, recession probability, and sector
    rotation signal for the given ticker.  Returns empty string if macro
    data is unavailable.

    Same pattern as insiders.py's insider_prompt_block() -- returns a string
    that gets concatenated into the LLM prompt in analysis.py.

    Parameters
    ----------
    ticker : str -- stock symbol

    Returns
    -------
    str -- formatted prompt block, or '' if no data available
    """
    try:
        regime_data = get_macro_regime()
        regime = regime_data.get("regime", "NEUTRAL")
        confidence = regime_data.get("confidence", 0.0)
        signals = regime_data.get("signals", {})

        # Check if we have any real data
        if signals.get("note") and "unavailable" in signals["note"].lower():
            return ""

        # Get sector for this ticker
        sector = _lookup_sector(ticker)
        sector_str = sector or "Unknown"

        # Get sector rotation signal
        rotation = get_sector_rotation_signal(sector_str) if sector else {}
        signal = rotation.get("signal", "NEUTRAL")
        reason = rotation.get("reason", "")

        # Recession probability
        recession_prob = get_recession_probability()

        # Macro score
        macro_score = compute_macro_score(ticker, sector=sector)

        # Build the prompt block
        yc_str = signals.get("yield_curve", "N/A")
        fr_str = signals.get("fed_rate", "N/A")

        block = (
            f"\nMacro Environment: "
            f"regime={regime} (confidence={confidence:.0%}), "
            f"yield curve={yc_str}, "
            f"fed rate={fr_str}, "
            f"recession probability={recession_prob:.0%}. "
            f"Sector: {sector_str} -> {signal} ({reason}). "
            f"Macro tailwind score: {macro_score:.0f}/100."
        )
        return block

    except Exception:
        return ""

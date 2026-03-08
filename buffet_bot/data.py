"""Data-fetching helpers — yfinance, Alpaca, FRED, Nasdaq earnings."""
import json
import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import yfinance as yf
import ollama
from click.shell_completion import CompletionItem

from buffet_bot.globals import (
    API_KEY, SECRET_KEY, data_client, console,
    MODEL_COLORS, FRED_API_KEY, NASDAQ_EARNINGS_HEADERS,
)
from buffet_bot.globals import (
    StockLatestQuoteRequest, StockLatestBarRequest,
)
from buffet_bot.db import get_watchlist
from buffet_bot.universe import _COMPANY_DB as _UNIVERSE_DB  # type: ignore

# Limit concurrent yfinance requests to prevent crumb/session corruption and SQLite lock errors
_yf_semaphore = threading.Semaphore(2)


def get_buffett_metrics(ticker):
    """Calculate expanded Buffett value score across 6 criteria (100 pts max)."""
    try:
        with _yf_semaphore:
            t = yf.Ticker(ticker)
            info = t.info

        def _f(key, fallback=0.0):
            v = info.get(key)
            try:
                return float(v) if v is not None else float(fallback)
            except (TypeError, ValueError):
                return float(fallback)

        roe        = _f('returnOnEquity') * 100        # %
        debt_eq    = _f('debtToEquity', 999)            # ratio
        op_margin  = _f('operatingMargins') * 100      # %
        roa        = _f('returnOnAssets') * 100         # ROIC proxy %
        pe         = _f('trailingPE')
        pb         = _f('priceToBook')
        fcf        = _f('freeCashflow')
        mkt_cap    = _f('marketCap', 1)
        div_yield  = _f('dividendYield') * 100          # %
        eg_1y      = _f('earningsGrowth') * 100         # %

        fcf_yield = (fcf / mkt_cap * 100) if mkt_cap > 0 else 0.0

        score = 0
        metrics: dict = {}

        if roe > 15:        score += 25; metrics['roe_pass'] = True
        if roa > 12:        score += 20; metrics['roic_pass'] = True
        if debt_eq < 50:    score += 15; metrics['debt_pass'] = True
        if op_margin > 10:  score += 15; metrics['margin_pass'] = True
        if fcf_yield > 3:   score += 15; metrics['fcf_pass'] = True
        if 0 < pe < 25:     score += 5;  metrics['pe_pass'] = True
        if 0 < pb < 5:      score += 3;  metrics['pb_pass'] = True
        if div_yield > 1.5: score += 2;  metrics['div_pass'] = True

        beta = float(info.get('beta') or 1.0)
        metrics.update({
            'score':        score,
            'roe':          round(roe, 1),
            'roic':         round(roa, 1),
            'debt_eq':      round(debt_eq, 1),
            'op_margin':    round(op_margin, 1),
            'fcf_yield':    round(fcf_yield, 1),
            'pe':           round(pe, 1),
            'pb':           round(pb, 2),
            'div_yield':    round(div_yield, 2),
            'eg_1y':        round(eg_1y, 1),
            'beta':         round(beta, 2),
        })
        return metrics
    except Exception as e:
        console.print(f"[red]Metrics error for {ticker}: {e}[/red]")
        return {'score': 0}


def get_analyst_consensus(ticker: str) -> dict:
    """Fetch Wall Street analyst consensus and price targets via yfinance."""
    _RATING_MAP = {
        'strong_buy':   'STRONG BUY',
        'buy':          'BUY',
        'hold':         'HOLD',
        'underperform': 'UNDERPERFORM',
        'sell':         'SELL',
    }
    try:
        with _yf_semaphore:
            t    = yf.Ticker(ticker)
            info = t.info

            target_mean   = info.get('targetMeanPrice')
            target_low    = info.get('targetLowPrice')
            target_high   = info.get('targetHighPrice')
            current       = info.get('currentPrice') or info.get('regularMarketPrice')
            num_analysts  = info.get('numberOfAnalystOpinions')
            rating_raw    = info.get('recommendationMean')
            rating_key_in = (info.get('recommendationKey') or '').lower().replace(' ', '_')
            rating_key    = _RATING_MAP.get(rating_key_in, rating_key_in.upper() or 'N/A')

            if not target_mean:
                return {}

            upside_pct = round((float(target_mean) - float(current)) / float(current) * 100, 1) \
                         if target_mean and current else None

            recent_changes: list[dict] = []
            try:
                ud = t.upgrades_downgrades
                if ud is not None and not ud.empty:
                    for _, row in ud.head(5).iterrows():
                        recent_changes.append({
                            'firm':   str(row.get('Firm', '')),
                            'from':   str(row.get('FromGrade', '')),
                            'to':     str(row.get('ToGrade', '')),
                            'action': str(row.get('Action', '')),
                        })
            except Exception:
                pass

            return {
                'rating_key':      rating_key,
                'rating_mean':     round(float(rating_raw), 2) if rating_raw else None,
                'target_mean':     round(float(target_mean), 2),
                'target_low':      round(float(target_low),  2) if target_low  else None,
                'target_high':     round(float(target_high), 2) if target_high else None,
                'current_price':   round(float(current),     2) if current     else None,
                'upside_pct':      upside_pct,
                'num_analysts':    int(num_analysts) if num_analysts else None,
                'recent_changes':  recent_changes,
            }
    except Exception:
        return {}


def get_tech_indicators(ticker):
    """Basic RSI/MACD for high-risk"""
    try:
        import yfinance as yf
        with _yf_semaphore:
            data = yf.download(ticker, period='3mo', progress=False)
        if data.empty:
            return {}
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs)).iloc[-1]
        ema12 = data['Close'].ewm(span=12).mean()
        ema26 = data['Close'].ewm(span=26).mean()
        macd = ema12 - ema26
        return {'rsi': round(rsi, 1), 'macd': round(macd.iloc[-1], 4)}
    except Exception:
        return {}


def get_realtime_data(ticker):
    """Fetch current price and today's OHLCV bar from Alpaca Data API."""
    try:
        quote_req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        bar_req = StockLatestBarRequest(symbol_or_symbols=ticker)
        quote = data_client.get_stock_latest_quote(quote_req)[ticker]
        bar = data_client.get_stock_latest_bar(bar_req)[ticker]
        mid = round((float(quote.ask_price) + float(quote.bid_price)) / 2, 2)
        open_price = float(bar.open)
        change_pct = round((mid - open_price) / open_price * 100, 2) if open_price else 0
        return {
            'price': mid,
            'open': open_price,
            'high': float(bar.high),
            'low': float(bar.low),
            'volume': int(bar.volume),
            'change_pct': change_pct,
            'source': 'alpaca',
        }
    except Exception:
        pass
    # Fallback to yfinance
    try:
        with _yf_semaphore:
            fi = yf.Ticker(ticker).fast_info
        price = float(fi.last_price)
        open_price = float(fi.open) if fi.open else price
        change_pct = round((price - open_price) / open_price * 100, 2) if open_price else 0
        return {
            'price': price,
            'open': open_price,
            'high': float(fi.day_high) if fi.day_high else price,
            'low': float(fi.day_low) if fi.day_low else price,
            'volume': int(fi.three_month_average_volume) if fi.three_month_average_volume else 0,
            'change_pct': change_pct,
            'source': 'yfinance',
        }
    except Exception as e:
        console.print(f"[dim red]Live quote unavailable for {ticker}: {e}[/dim red]")
        return {}


def _fetch_fred_data() -> dict:
    """Fetch latest Fed rate, yield curve, and CPI from FRED (3 concurrent requests)."""
    if not FRED_API_KEY:
        return {}
    series = {'DFF': 'fed_rate', 'T10Y2Y': 'yield_curve', 'CPIAUCSL': 'cpi'}

    def _fetch_one(series_id, key):
        try:
            r = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    'series_id': series_id,
                    'api_key':   FRED_API_KEY,
                    'file_type': 'json',
                    'sort_order': 'desc',
                    'limit': 1,
                },
                timeout=5,
            )
            r.raise_for_status()
            val = r.json()['observations'][0]['value']
            return key, float(val)
        except Exception:
            return key, None

    result = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = [ex.submit(_fetch_one, sid, key) for sid, key in series.items()]
        for f in as_completed(futs):
            k, v = f.result()
            if v is not None:
                result[k] = v
    return result


def _get_earnings_date(ticker: str) -> dict | None:
    """Check Nasdaq earnings calendar for the next 7 days. Returns dict or None."""
    for days_ahead in range(8):
        date_str = (datetime.utcnow() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
        try:
            r = requests.get(
                "https://api.nasdaq.com/api/calendar/earnings",
                params={"date": date_str},
                headers=NASDAQ_EARNINGS_HEADERS,
                timeout=5,
            )
            r.raise_for_status()
            rows = r.json().get('data', {}).get('rows') or []
            for row in rows:
                if row.get('symbol', '').upper() == ticker.upper():
                    return {
                        'date':           date_str,
                        'time':           row.get('time', ''),
                        'eps_forecast':   row.get('epsForecast', ''),
                        'fiscal_quarter': row.get('fiscalQuarterEnding', ''),
                    }
        except Exception:
            pass
        time.sleep(0.05)
    return None


def get_recent_news(ticker, limit=5):
    """Fetch recent news headlines from Alpaca News API."""
    try:
        url = "https://data.alpaca.markets/v1beta1/news"
        headers = {
            'APCA-API-KEY-ID': API_KEY,
            'APCA-API-SECRET-KEY': SECRET_KEY,
        }
        params = {'symbols': ticker, 'limit': limit, 'sort': 'desc'}
        resp = requests.get(url, headers=headers, params=params, timeout=5)
        resp.raise_for_status()
        return [
            {
                'headline': item.get('headline', ''),
                'summary': item.get('summary', ''),
                'published_at': item.get('updated_at', '')[:10],
            }
            for item in resp.json().get('news', [])
        ]
    except Exception:
        return []


def analyze_news_sentiment(news_items, ticker, primary_model):
    """Score news headlines with the LLM. Returns aggregate sentiment dict."""
    _neutral = {'overall': 'neutral', 'score': 0.0, 'count': 0, 'items': []}
    if not news_items:
        return _neutral

    headlines = "\n".join(
        f"{i+1}. [{item['published_at']}] {item['headline']}"
        for i, item in enumerate(news_items[:10])
    )
    prompt = (
        f"Analyze the sentiment of each headline for {ticker}. "
        "Return ONLY valid JSON with no extra text:\n"
        '{"items": [{"headline": "...", "sentiment": "positive|neutral|negative", '
        '"confidence": 0.85, "impact": "high|medium|low"}], '
        '"overall_sentiment": "positive|neutral|negative", "sentiment_score": 0.65}\n\n'
        f"Headlines:\n{headlines}"
    )
    try:
        color = MODEL_COLORS.get(primary_model, 'white')
        with console.status(f"[{color}]Analyzing news sentiment ({primary_model})...[/{color}]"):
            resp = ollama.chat(
                model=primary_model,
                messages=[{'role': 'user', 'content': prompt}],
                options={'temperature': 0.1},
            )
        content = resp['message']['content'].strip()
        start, end = content.find('{'), content.rfind('}') + 1
        if start < 0 or end <= start:
            return _neutral
        data = json.loads(content[start:end])
        raw_score = float(data.get('sentiment_score', 0.0))
        return {
            'overall': data.get('overall_sentiment', 'neutral'),
            'score':   max(-1.0, min(1.0, raw_score)),
            'count':   len(news_items),
            'items':   data.get('items', []),
        }
    except Exception:
        return _neutral


# ── Shell completion ──────────────────────────────────────────────────────────

_COMMON_TICKERS = (
    list(_UNIVERSE_DB.keys())
    + ['BTC/USD', 'ETH/USD', 'SOL/USD', 'DOGE/USD', 'ADA/USD']
)


def _complete_ticker(ctx, param, incomplete):
    """Return watchlist tickers + common tickers for shell tab completion."""
    try:
        watchlist = [row['ticker'] for row in get_watchlist()]
    except Exception:
        watchlist = []
    candidates = list(dict.fromkeys(watchlist + _COMMON_TICKERS))
    return [CompletionItem(t) for t in candidates if t.upper().startswith(incomplete.upper())]

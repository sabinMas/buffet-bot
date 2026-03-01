# Data Sources — Buffet-Bot

> Owned by: Web Scraper Agent
> Purpose: Document every evaluated external data source — viable or rejected.
> Last updated: 2026-02-28

---

## How to Add an Entry

```markdown
### [Source Name]
- **URL:** https://...
- **Data type:** fundamentals / technicals / news / macro / filings / options / etc.
- **Auth required:** none / API key (free) / OAuth
- **Rate limit:** X requests/min or X requests/day
- **Key endpoints:**
  - `GET /endpoint` — what it returns
- **Python integration sketch:** (brief)
- **Relevant for:** which Buffet-Bot feature
- **Caveats:** delays, ToS restrictions, coverage gaps
- **Status:** ✅ Viable | ⚠️ Marginal | ❌ Rejected
- **Rejection reason:** (if rejected)
```

---

## Already Integrated (do not re-document)

| Source | What it provides | Where in code |
|--------|-----------------|---------------|
| `yfinance` | Fundamentals, 6-month history, RSI/MACD | `get_buffett_metrics()`, `get_tech_indicators()` |
| Alpaca Data API | Real-time quote, OHLCV bar | `_print_live_market()` |
| Alpaca News API | Recent headlines | `_print_live_market()` |
| Alpaca Trading API | Orders, account, portfolio history | `_place_order()`, `status`, `portfolio` |
| House Stock Watcher S3 | Congressional trades | `buffet_bot/politicians.py` |
| FMP API | Congressional trades (secondary) | `buffet_bot/politicians.py` |

---

## Evaluated Sources

---

### FRED (Federal Reserve Economic Data)

- **URL:** `https://api.stlouisfed.org/fred/`
- **Data type:** Macro — interest rates, yield curve, inflation
- **Auth required:** API key (free, no credit card — register at `https://fredaccount.stlouisfed.org`, issued instantly)
- **Rate limit:** 120 requests/minute. No daily cap. Returns HTTP 429 if exceeded.
- **Key endpoints:**
  - `GET https://api.stlouisfed.org/fred/series/observations?series_id=DFF&api_key=KEY&file_type=json&sort_order=desc&limit=1`
  - Returns latest observation for any FRED series. Add `units=pc1` for YoY % change.
  - Three series to pull per `analyze` run (3 requests total — well within rate limit):

  | Series ID | Metric | Frequency | Notes |
  |-----------|--------|-----------|-------|
  | `DFF` | Daily Federal Funds Rate | Daily | Prefer over `FEDFUNDS` (monthly) |
  | `T10Y2Y` | 10Y−2Y Treasury yield spread | Daily | Gaps on weekends/holidays |
  | `CPIAUCSL` | CPI All Urban (seasonally adj.) | Monthly | ~6 week lag |

- **Example response snippet:**
  ```json
  { "observations": [{ "date": "2026-02-27", "value": "4.33" }] }
  ```
- **Python integration sketch:**
  ```python
  FRED_API_KEY = os.getenv('FRED_API_KEY', '')

  def _fetch_fred_data() -> dict:
      if not FRED_API_KEY:
          return {}
      series = {'DFF': 'fed_rate', 'T10Y2Y': 'yield_curve', 'CPIAUCSL': 'cpi'}
      result = {}
      for series_id, key in series.items():
          try:
              r = requests.get(
                  "https://api.stlouisfed.org/fred/series/observations",
                  params={'series_id': series_id, 'api_key': FRED_API_KEY,
                          'file_type': 'json', 'sort_order': 'desc', 'limit': 1},
                  timeout=5,
              )
              val = r.json()['observations'][0]['value']
              result[key] = float(val)  # value is always a string; "." = missing data
          except Exception:
              pass
      return result
  ```
- **Relevant for:** Injecting macro context into the LLM prompt in `_run_analysis()`. Improves model reasoning on rate-sensitive stocks (banks, REITs, bonds). Add `fed_rate`, `yield_curve`, `cpi` to the prompt's macro block.
- **Required attribution (ToS):** Must display in README and optionally in `analyze` output footer — *"This product uses the FRED API but is not endorsed or certified by the Federal Reserve Bank of St. Louis."*
- **Caveats:**
  - `value` is **always a string** — cast with `float(val)`; `"."` means missing, catch `ValueError`
  - `T10Y2Y` has weekend/holiday gaps — do not assume the latest date is today
  - `CPIAUCSL` lags ~6 weeks — show the observation date alongside the value in the prompt
  - ToS permits use in trading assistants; prohibits redistributing bulk FRED data publicly
  - Add `FRED_API_KEY` to `.env` — degrade gracefully (return `{}`) if not set
- **Status:** ✅ Viable

---

### Nasdaq Earnings Calendar

- **URL:** `https://api.nasdaq.com/api/calendar/earnings`
- **Data type:** Earnings calendar — upcoming/past earnings dates, EPS estimates vs. actuals
- **Auth required:** No API key. However, **browser-like HTTP headers are mandatory** — requests without them return HTTP 403.
- **Rate limit:** Not documented (undocumented internal endpoint powering Nasdaq's own site). Do not poll more than once per 60 seconds per IP to avoid Cloudflare blocking.
- **Key endpoints:**
  - `GET https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD`
  - Returns all companies reporting on that date. Query today + up to 7 days ahead to find next earnings for a ticker.
  - **Required headers** (without these → HTTP 403):
    ```python
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
        "Accept": "application/json, text/plain, */*",
    }
    ```
- **Response structure:**
  ```json
  {
    "data": {
      "rows": [
        {
          "symbol": "AAPL",
          "name": "Apple Inc.",
          "time": "time-after-hours",
          "epsForecast": "$2.35",
          "fiscalQuarterEnding": "Dec/2025",
          "lastYearEPS": "$2.18",
          "lastYearRptDt": "2/1/2024",
          "noOfEsts": "28",
          "marketCap": "3,100,000,000,000"
        }
      ]
    },
    "status": { "rCode": 200 }
  }
  ```
  | Field | Notes |
  |-------|-------|
  | `time` | `"time-pre-market"` / `"time-after-hours"` / `"time-not-supplied"` |
  | `epsForecast` | String dollar value or `""` / `"N/A"` — parse defensively |
  | `fiscalQuarterEnding` | `"Mon/YYYY"` format — use `strptime(s, "%b/%Y")` if parsing |
  | All numeric fields | Always strings — never cast without try/except |

- **Python integration sketch:**
  ```python
  NASDAQ_EARNINGS_HEADERS = {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      "Origin": "https://www.nasdaq.com",
      "Referer": "https://www.nasdaq.com/",
      "Accept": "application/json, text/plain, */*",
  }

  def _get_earnings_date(ticker: str) -> dict | None:
      """Return upcoming earnings info for ticker within next 7 days, or None."""
      for days_ahead in range(8):
          date_str = (datetime.utcnow() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
          try:
              r = requests.get(
                  "https://api.nasdaq.com/api/calendar/earnings",
                  params={"date": date_str},
                  headers=NASDAQ_EARNINGS_HEADERS,
                  timeout=5,
              )
              rows = r.json().get('data', {}).get('rows') or []
              for row in rows:
                  if row.get('symbol', '').upper() == ticker.upper():
                      return {
                          'date': date_str,
                          'time': row.get('time', ''),
                          'eps_forecast': row.get('epsForecast', ''),
                          'fiscal_quarter': row.get('fiscalQuarterEnding', ''),
                      }
          except Exception:
              pass
      return None
  ```
- **Relevant for:** Showing upcoming earnings date in `analyze` output, before LLM panels. Warns user of binary event risk before a BUY recommendation is shown.
- **Caveats:**
  - **Undocumented internal endpoint** — no SLA, Nasdaq can change/remove without notice
  - Returns empty `rows` (not an error) for weekends and dates with no earnings
  - Up to 8 requests per `analyze` call in worst case — add `time.sleep(0.1)` between loop iterations
  - All numeric fields are strings — always parse defensively
  - Gray area on ToS — acceptable for a personal paper trading tool at low polling frequency
- **Status:** ⚠️ Marginal (viable for v0.4.1 but monitor for endpoint breakage)

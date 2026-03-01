# Integration Tickets — Buffet-Bot

> Owned by: Web Scraper Agent (writes) → Software Engineer (implements)
> Purpose: Handoff specs from scraper research to ENG implementation.
> Last updated: 2026-02-28

---

## Status Key

- `[ ]` Open — ready for ENG to pick up
- `[~]` In progress
- `[x]` Implemented
- `[blocked]` Waiting on dependency

---

## How to Write a Ticket

```markdown
## TICKET-NNN: [Source] — [Feature it enables]

**Priority:** high / medium / low
**Assigned to:** Software Engineer
**Status:** [ ] open
**Depends on:** (any architect approval or other ticket)

### What to integrate
Brief description.

### Endpoints to call
- `GET https://...` — returns {...}

### Where in main.py to add it
- New function name + pattern to follow
- Called from: which command(s)

### New packages required
- none / `package>=version`

### Example Python sketch
(minimal fetch + parse)

### Caveats / risks
```

---

## Open Tickets

---

## TICKET-001: FRED API — Macro context in LLM prompt

**Priority:** high
**Assigned to:** Software Engineer
**Status:** [x] implemented — 2026-03-01
**Depends on:** `FRED_API_KEY` added to `.env` by user (free, no credit card)

### What to integrate

Fetch the three latest FRED macro readings — fed funds rate (`DFF`), yield curve spread (`T10Y2Y`), and CPI (`CPIAUCSL`) — and inject them as a `macro_block` string into the LLM prompt inside `_run_analysis()`. This gives the models interest rate and inflation context when making BUY/SELL/HOLD decisions on rate-sensitive stocks.

### Endpoints to call

```
GET https://api.stlouisfed.org/fred/series/observations
  ?series_id=DFF          (or T10Y2Y or CPIAUCSL)
  &api_key={FRED_API_KEY}
  &file_type=json
  &sort_order=desc
  &limit=1
```

Three separate requests, one per series. All return the same response shape.

### Where in main.py to add it

- **New constant** (near `DB_PATH`): `FRED_API_KEY = os.getenv('FRED_API_KEY', '')`
- **New function** `_fetch_fred_data() -> dict` — add near the other data-fetching functions (lines 219–410). Returns `{'fed_rate': float, 'yield_curve': float, 'cpi': float}` or `{}` if key missing/network fails.
- **Call from** `_run_analysis()` alongside the other data fetches. Build a `macro_block` string and append it to the existing prompt. Example:
  ```python
  macro = _fetch_fred_data()
  macro_block = ""
  if macro:
      macro_block = (
          f"\nMacro context: Fed rate {macro.get('fed_rate','N/A')}%, "
          f"yield curve (10Y-2Y) {macro.get('yield_curve','N/A')}%, "
          f"CPI index {macro.get('cpi','N/A')}."
      )
  ```

### New packages required

None — uses `requests` (already a dependency).

### Example Python sketch

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
                params={
                    'series_id': series_id,
                    'api_key': FRED_API_KEY,
                    'file_type': 'json',
                    'sort_order': 'desc',
                    'limit': 1,
                },
                timeout=5,
            )
            val = r.json()['observations'][0]['value']
            result[key] = float(val)
        except Exception:
            pass
    return result
```

### Caveats / risks

- `value` field is always a string — cast with `float(val)`. The literal string `"."` means missing data and will raise `ValueError` — the `except Exception: pass` handles this.
- `T10Y2Y` gaps on weekends/holidays — the latest value may be from Friday. Acceptable.
- `CPIAUCSL` lags ~6 weeks. Not a problem — it's background context, not a real-time signal.
- Degrades gracefully: if `FRED_API_KEY` is not set, `_fetch_fred_data()` returns `{}` and `macro_block` stays `""` — no impact on existing behavior.
- **Required attribution in README:** *"This product uses the FRED API but is not endorsed or certified by the Federal Reserve Bank of St. Louis."*

---

## TICKET-002: Nasdaq Earnings Calendar — Upcoming earnings date in `analyze`

**Priority:** medium
**Assigned to:** Software Engineer
**Status:** [x] implemented — 2026-03-01
**Depends on:** nothing (no API key required)

### What to integrate

Before showing LLM analysis panels in the `analyze` command, check whether the ticker has earnings scheduled in the next 7 days. If so, display a yellow warning — earnings are a high-impact binary event that can invalidate any technical/fundamental thesis within hours.

### Endpoints to call

```
GET https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD
```

Query once per day offset (0–7) until a match is found for the ticker, or all 8 requests return no match.

**Headers are mandatory** — without them the endpoint returns HTTP 403:
```python
NASDAQ_EARNINGS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
    "Accept": "application/json, text/plain, */*",
}
```

### Where in main.py to add it

- **New module-level constant** `NASDAQ_EARNINGS_HEADERS` (near other constants at top of file)
- **New function** `_get_earnings_date(ticker: str) -> dict | None` — add near `get_recent_news()`. Returns `{'date': 'YYYY-MM-DD', 'time': str, 'eps_forecast': str, 'fiscal_quarter': str}` or `None` if no upcoming earnings found.
- **Call from `analyze` command** — after `_print_live_market()`, before LLM panels:
  ```python
  earnings = _get_earnings_date(ticker)
  if earnings:
      timing = earnings['time'].replace('time-', '').replace('-', ' ')
      console.print(Panel(
          f"[bold yellow]Earnings in {(datetime.strptime(earnings['date'], '%Y-%m-%d') - datetime.utcnow()).days + 1} day(s)[/bold yellow] "
          f"({timing}) — {earnings['fiscal_quarter']}  EPS est: {earnings['eps_forecast']}",
          title="[bold yellow]Upcoming Earnings Warning[/bold yellow]",
          border_style="yellow",
      ))
  ```

### New packages required

None — uses `requests` and stdlib `datetime` (both already available).

### Example Python sketch

```python
def _get_earnings_date(ticker: str) -> dict | None:
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

### Caveats / risks

- **Undocumented endpoint** — no SLA. If it breaks, `_get_earnings_date()` returns `None` silently and `analyze` continues unaffected.
- All numeric fields in the response are strings — never cast without try/except.
- Up to 8 HTTP requests in worst case (no earnings found in 7-day window). Add `time.sleep(0.05)` between loop iterations to be polite.
- Returns empty `rows` (not an error) for weekends — the loop handles this naturally.
- Do NOT call this for crypto symbols — check `is_crypto_symbol(ticker)` and skip if true.

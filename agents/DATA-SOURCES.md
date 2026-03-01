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

*(Web Scraper: add entries below as you evaluate each source)*

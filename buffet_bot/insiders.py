"""
SEC EDGAR Form 4 insider transaction fetcher.

No API key required. Uses three public EDGAR endpoints:
  1. https://www.sec.gov/files/company_tickers.json  — ticker → CIK lookup
  2. https://data.sec.gov/submissions/CIK{n}.json    — recent filings index
  3. https://www.sec.gov/Archives/edgar/data/…       — individual Form 4 XML

Form 4 rules: insiders (directors, officers, >10% holders) must report
equity transactions within 2 business days — a leading signal that
Buffett-style analysts track closely.
"""
import xml.etree.ElementTree as ET
import requests
from datetime import datetime, timedelta

_HEADERS = {
    "User-Agent": "buffet-bot/1.0 research-tool noreply@buffet-bot.local",
    "Accept-Encoding": "gzip, deflate",
}
_TIMEOUT = 10

# ── CIK lookup ────────────────────────────────────────────────────────────────

_cik_cache: dict[str, str] = {}


def get_cik(ticker: str) -> str | None:
    """Return zero-padded 10-digit CIK for ticker, or None if not found."""
    t = ticker.upper()
    if t in _cik_cache:
        return _cik_cache[t]
    try:
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        for entry in r.json().values():
            if entry.get("ticker", "").upper() == t:
                cik = str(entry["cik_str"]).zfill(10)
                _cik_cache[t] = cik
                return cik
    except Exception:
        pass
    return None


# ── Filing index ──────────────────────────────────────────────────────────────

def _get_filing_urls(cik: str, max_filings: int = 20) -> list[dict]:
    """Return [{date, url}] for the most recent Form 4 filings."""
    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        recent = r.json().get("filings", {}).get("recent", {})
        forms  = recent.get("form", [])
        accs   = recent.get("accessionNumber", [])
        dates  = recent.get("filingDate", [])
        docs   = recent.get("primaryDocument", [])

        results = []
        for i, form in enumerate(forms):
            if form != "4":
                continue
            acc_clean = accs[i].replace("-", "")
            url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{acc_clean}/{docs[i]}"
            )
            results.append({"date": dates[i], "url": url})
            if len(results) >= max_filings:
                break
        return results
    except Exception:
        return []


# ── Form 4 XML parser ─────────────────────────────────────────────────────────

def _parse_form4(url: str) -> list[dict]:
    """Download and parse one Form 4 XML. Returns list of transaction dicts."""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception:
        return []

    # Insider identity
    name          = (root.findtext(".//rptOwnerName") or "Unknown").title()
    is_officer    = root.findtext(".//isOfficer",  "0") == "1"
    is_director   = root.findtext(".//isDirector", "0") == "1"
    officer_title = (root.findtext(".//officerTitle") or "").strip()

    if is_officer and officer_title:
        role = officer_title
    elif is_director:
        role = "Director"
    else:
        role = "Other"

    txns = []
    for txn in root.findall(".//nonDerivativeTransaction"):
        try:
            date       = txn.findtext(".//transactionDate/value", "")
            shares_raw = txn.findtext(".//transactionShares/value", "0") or "0"
            price_raw  = txn.findtext(".//transactionPricePerShare/value", "0") or "0"
            code       = txn.findtext(".//transactionAcquiredDisposedCode/value", "").upper()

            shares = int(float(shares_raw))
            price  = float(price_raw)
            value  = shares * price

            if shares == 0:
                continue  # skip phantom rows (e.g. option exercises with $0 price listed separately)

            txns.append({
                "name":   name,
                "role":   role,
                "date":   date,
                "shares": shares,
                "price":  price,
                "value":  value,
                "type":   "BUY" if code == "A" else "SELL",
            })
        except Exception:
            continue

    return txns


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_insider_transactions(
    ticker: str,
    days: int = 90,
    limit: int = 15,
    max_filings: int = 20,
) -> list[dict]:
    """
    Return up to `limit` Form 4 transactions for `ticker` in the last `days`.

    Each dict: {name, role, date, shares, price, value, type}.
    Returns [] on any network/parse failure (non-blocking).
    """
    cik = get_cik(ticker)
    if not cik:
        return []

    filing_urls = _get_filing_urls(cik, max_filings=max_filings)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    all_txns: list[dict] = []
    for f in filing_urls:
        if f["date"] < cutoff:
            continue
        all_txns.extend(_parse_form4(f["url"]))
        if len(all_txns) >= limit * 2:   # over-fetch then trim
            break

    # Deduplicate by (name, date, shares, type)
    seen: set[tuple] = set()
    unique: list[dict] = []
    for t in sorted(all_txns, key=lambda x: x["date"], reverse=True):
        key = (t["name"], t["date"], t["shares"], t["type"])
        if key not in seen:
            seen.add(key)
            unique.append(t)
        if len(unique) >= limit:
            break

    return unique


def insider_summary(transactions: list[dict]) -> dict:
    """
    Aggregate buy/sell counts and net dollar value.
    Returns {buys, sells, net_value, net_sentiment}.
    net_sentiment: 'bullish' | 'neutral' | 'bearish'
    """
    buys  = [t for t in transactions if t["type"] == "BUY"]
    sells = [t for t in transactions if t["type"] == "SELL"]
    net   = sum(t["value"] for t in buys) - sum(t["value"] for t in sells)
    if net > 50_000:
        sentiment = "bullish"
    elif net < -50_000:
        sentiment = "bearish"
    else:
        sentiment = "neutral"
    return {
        "buys":          len(buys),
        "sells":         len(sells),
        "net_value":     net,
        "net_sentiment": sentiment,
    }


def insider_prompt_block(transactions: list[dict]) -> str:
    """
    Compact one-line summary for injection into the LLM prompt.
    Returns '' when there are no transactions.
    """
    if not transactions:
        return ""
    summ    = insider_summary(transactions)
    net_str = f"{'+' if summ['net_value'] >= 0 else ''}${summ['net_value']:,.0f}"
    recent  = "; ".join(
        f"{t['name'].split()[-1]} {t['type']} {t['shares']:,}sh "
        f"@ ${t['price']:.2f} ({t['date']})"
        for t in transactions[:3]
    )
    return (
        f"\nInsider Activity (Form 4, 60d): "
        f"{summ['buys']} buys / {summ['sells']} sells — net {net_str} "
        f"({summ['net_sentiment'].upper()}). Recent: {recent}"
    )


# ── Rich display ──────────────────────────────────────────────────────────────

def display_insider_table(ticker: str, transactions: list[dict], console) -> None:
    """Render a Rich summary panel + transaction table."""
    from rich.table import Table
    from rich.panel import Panel
    from rich import box as _box

    if not transactions:
        console.print(
            f"[yellow]No Form 4 insider transactions found for {ticker} "
            f"in the last 90 days.[/yellow]"
        )
        return

    summ       = insider_summary(transactions)
    net_color  = "green" if summ["net_value"] >= 0 else "red"
    net_sign   = "+" if summ["net_value"] >= 0 else ""
    sent_color = {"bullish": "green", "neutral": "yellow", "bearish": "red"}[summ["net_sentiment"]]

    console.print(Panel(
        f"Buys:  [bold green]{summ['buys']}[/bold green]  "
        f"Sells: [bold red]{summ['sells']}[/bold red]  "
        f"Net Value: [{net_color}][bold]{net_sign}${summ['net_value']:,.0f}[/bold][/{net_color}]  "
        f"Sentiment: [{sent_color}][bold]{summ['net_sentiment'].upper()}[/bold][/{sent_color}]",
        title=f"[bold]{ticker}[/bold] Insider Activity — Form 4 (90d)",
        border_style="blue",
    ))

    tbl = Table(box=_box.ROUNDED, header_style="bold blue")
    tbl.add_column("Date",    style="dim",      min_width=11, no_wrap=True)
    tbl.add_column("Insider", min_width=22)
    tbl.add_column("Role",    style="dim",      min_width=20)
    tbl.add_column("Type",    justify="center", min_width=6)
    tbl.add_column("Shares",  justify="right",  min_width=10)
    tbl.add_column("Price",   justify="right",  min_width=9)
    tbl.add_column("Value",   justify="right",  min_width=13)

    for t in transactions:
        clr = "green" if t["type"] == "BUY" else "red"
        tbl.add_row(
            t["date"],
            t["name"],
            t["role"],
            f"[{clr}][bold]{t['type']}[/bold][/{clr}]",
            f"{t['shares']:,}",
            f"${t['price']:.2f}"  if t["price"] else "—",
            f"${t['value']:,.0f}" if t["value"] else "—",
        )

    console.print(tbl)
    console.print("[dim]Source: SEC EDGAR Form 4 filings (sec.gov) — no API key required[/dim]")

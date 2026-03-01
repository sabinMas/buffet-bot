"""
Investable company universe — bundled database + live EDGAR search.

Bundled DB: ~500 well-known US-listed companies across all 11 GICS sectors.
  - Zero network required, instant, works offline.
  - Each entry: {name, sector, exchange}

EDGAR search: live SEC EDGAR company_tickers.json — 10,000+ registered companies.
  - Requires network; same endpoint used by buffet_bot/insiders.py.
  - Cached in-process after first fetch.
"""
import requests

_HEADERS = {
    "User-Agent": "buffet-bot/1.0 research-tool noreply@buffet-bot.local",
    "Accept-Encoding": "gzip, deflate",
}
_TIMEOUT = 12

# ── Bundled company database ──────────────────────────────────────────────────
# ~500 US-listed companies across all 11 GICS sectors.
# Format: ticker -> {name, sector, exchange}

_COMPANY_DB: dict[str, dict] = {
    # ── Technology ────────────────────────────────────────────────────────────
    "AAPL":  {"name": "Apple Inc.",                    "sector": "Technology", "exchange": "NASDAQ"},
    "MSFT":  {"name": "Microsoft Corp",                "sector": "Technology", "exchange": "NASDAQ"},
    "NVDA":  {"name": "NVIDIA Corp",                   "sector": "Technology", "exchange": "NASDAQ"},
    "AMD":   {"name": "Advanced Micro Devices",        "sector": "Technology", "exchange": "NASDAQ"},
    "INTC":  {"name": "Intel Corp",                    "sector": "Technology", "exchange": "NASDAQ"},
    "AVGO":  {"name": "Broadcom Inc",                  "sector": "Technology", "exchange": "NASDAQ"},
    "QCOM":  {"name": "Qualcomm Inc",                  "sector": "Technology", "exchange": "NASDAQ"},
    "TXN":   {"name": "Texas Instruments",             "sector": "Technology", "exchange": "NASDAQ"},
    "MU":    {"name": "Micron Technology",             "sector": "Technology", "exchange": "NASDAQ"},
    "AMAT":  {"name": "Applied Materials",             "sector": "Technology", "exchange": "NASDAQ"},
    "LRCX":  {"name": "Lam Research",                  "sector": "Technology", "exchange": "NASDAQ"},
    "KLAC":  {"name": "KLA Corp",                      "sector": "Technology", "exchange": "NASDAQ"},
    "ADI":   {"name": "Analog Devices",                "sector": "Technology", "exchange": "NASDAQ"},
    "MRVL":  {"name": "Marvell Technology",            "sector": "Technology", "exchange": "NASDAQ"},
    "ON":    {"name": "ON Semiconductor",              "sector": "Technology", "exchange": "NASDAQ"},
    "STX":   {"name": "Seagate Technology",            "sector": "Technology", "exchange": "NASDAQ"},
    "WDC":   {"name": "Western Digital Corp",          "sector": "Technology", "exchange": "NASDAQ"},
    "SNPS":  {"name": "Synopsys Inc",                  "sector": "Technology", "exchange": "NASDAQ"},
    "CDNS":  {"name": "Cadence Design Systems",        "sector": "Technology", "exchange": "NASDAQ"},
    "ORCL":  {"name": "Oracle Corp",                   "sector": "Technology", "exchange": "NYSE"},
    "IBM":   {"name": "IBM Corp",                      "sector": "Technology", "exchange": "NYSE"},
    "CRM":   {"name": "Salesforce Inc",                "sector": "Technology", "exchange": "NYSE"},
    "NOW":   {"name": "ServiceNow Inc",                "sector": "Technology", "exchange": "NYSE"},
    "INTU":  {"name": "Intuit Inc",                    "sector": "Technology", "exchange": "NASDAQ"},
    "ADBE":  {"name": "Adobe Inc",                     "sector": "Technology", "exchange": "NASDAQ"},
    "PANW":  {"name": "Palo Alto Networks",            "sector": "Technology", "exchange": "NASDAQ"},
    "FTNT":  {"name": "Fortinet Inc",                  "sector": "Technology", "exchange": "NASDAQ"},
    "CRWD":  {"name": "CrowdStrike Holdings",          "sector": "Technology", "exchange": "NASDAQ"},
    "ZS":    {"name": "Zscaler Inc",                   "sector": "Technology", "exchange": "NASDAQ"},
    "OKTA":  {"name": "Okta Inc",                      "sector": "Technology", "exchange": "NASDAQ"},
    "DDOG":  {"name": "Datadog Inc",                   "sector": "Technology", "exchange": "NASDAQ"},
    "SNOW":  {"name": "Snowflake Inc",                 "sector": "Technology", "exchange": "NYSE"},
    "MDB":   {"name": "MongoDB Inc",                   "sector": "Technology", "exchange": "NASDAQ"},
    "TEAM":  {"name": "Atlassian Corp",                "sector": "Technology", "exchange": "NASDAQ"},
    "WDAY":  {"name": "Workday Inc",                   "sector": "Technology", "exchange": "NASDAQ"},
    "VEEV":  {"name": "Veeva Systems",                 "sector": "Technology", "exchange": "NYSE"},
    "HUBS":  {"name": "HubSpot Inc",                   "sector": "Technology", "exchange": "NYSE"},
    "PLTR":  {"name": "Palantir Technologies",         "sector": "Technology", "exchange": "NYSE"},
    "CSCO":  {"name": "Cisco Systems",                 "sector": "Technology", "exchange": "NASDAQ"},
    "ANET":  {"name": "Arista Networks",               "sector": "Technology", "exchange": "NYSE"},
    "HPQ":   {"name": "HP Inc",                        "sector": "Technology", "exchange": "NYSE"},
    "HPE":   {"name": "Hewlett Packard Enterprise",    "sector": "Technology", "exchange": "NYSE"},
    "DELL":  {"name": "Dell Technologies",             "sector": "Technology", "exchange": "NYSE"},
    "NTAP":  {"name": "NetApp Inc",                    "sector": "Technology", "exchange": "NASDAQ"},
    "PSTG":  {"name": "Pure Storage Inc",              "sector": "Technology", "exchange": "NYSE"},
    "ANSS":  {"name": "ANSYS Inc",                     "sector": "Technology", "exchange": "NASDAQ"},
    "EPAM":  {"name": "EPAM Systems",                  "sector": "Technology", "exchange": "NYSE"},
    "TWLO":  {"name": "Twilio Inc",                    "sector": "Technology", "exchange": "NYSE"},
    "ZM":    {"name": "Zoom Video Communications",     "sector": "Technology", "exchange": "NASDAQ"},
    "DOCU":  {"name": "DocuSign Inc",                  "sector": "Technology", "exchange": "NASDAQ"},

    # ── Healthcare ────────────────────────────────────────────────────────────
    "JNJ":   {"name": "Johnson & Johnson",             "sector": "Healthcare", "exchange": "NYSE"},
    "UNH":   {"name": "UnitedHealth Group",            "sector": "Healthcare", "exchange": "NYSE"},
    "LLY":   {"name": "Eli Lilly and Co",              "sector": "Healthcare", "exchange": "NYSE"},
    "PFE":   {"name": "Pfizer Inc",                    "sector": "Healthcare", "exchange": "NYSE"},
    "MRK":   {"name": "Merck & Co",                    "sector": "Healthcare", "exchange": "NYSE"},
    "ABT":   {"name": "Abbott Laboratories",           "sector": "Healthcare", "exchange": "NYSE"},
    "TMO":   {"name": "Thermo Fisher Scientific",      "sector": "Healthcare", "exchange": "NYSE"},
    "DHR":   {"name": "Danaher Corp",                  "sector": "Healthcare", "exchange": "NYSE"},
    "SYK":   {"name": "Stryker Corp",                  "sector": "Healthcare", "exchange": "NYSE"},
    "BSX":   {"name": "Boston Scientific",             "sector": "Healthcare", "exchange": "NYSE"},
    "EW":    {"name": "Edwards Lifesciences",          "sector": "Healthcare", "exchange": "NYSE"},
    "MDT":   {"name": "Medtronic PLC",                 "sector": "Healthcare", "exchange": "NYSE"},
    "ISRG":  {"name": "Intuitive Surgical",            "sector": "Healthcare", "exchange": "NASDAQ"},
    "HOLX":  {"name": "Hologic Inc",                   "sector": "Healthcare", "exchange": "NASDAQ"},
    "DXCM":  {"name": "DexCom Inc",                    "sector": "Healthcare", "exchange": "NASDAQ"},
    "GILD":  {"name": "Gilead Sciences",               "sector": "Healthcare", "exchange": "NASDAQ"},
    "AMGN":  {"name": "Amgen Inc",                     "sector": "Healthcare", "exchange": "NASDAQ"},
    "BIIB":  {"name": "Biogen Inc",                    "sector": "Healthcare", "exchange": "NASDAQ"},
    "REGN":  {"name": "Regeneron Pharmaceuticals",     "sector": "Healthcare", "exchange": "NASDAQ"},
    "VRTX":  {"name": "Vertex Pharmaceuticals",        "sector": "Healthcare", "exchange": "NASDAQ"},
    "MRNA":  {"name": "Moderna Inc",                   "sector": "Healthcare", "exchange": "NASDAQ"},
    "ABBV":  {"name": "AbbVie Inc",                    "sector": "Healthcare", "exchange": "NYSE"},
    "BMY":   {"name": "Bristol-Myers Squibb",          "sector": "Healthcare", "exchange": "NYSE"},
    "ILMN":  {"name": "Illumina Inc",                  "sector": "Healthcare", "exchange": "NASDAQ"},
    "A":     {"name": "Agilent Technologies",          "sector": "Healthcare", "exchange": "NYSE"},
    "IQV":   {"name": "IQVIA Holdings",                "sector": "Healthcare", "exchange": "NYSE"},
    "CRL":   {"name": "Charles River Laboratories",    "sector": "Healthcare", "exchange": "NYSE"},
    "CVS":   {"name": "CVS Health Corp",               "sector": "Healthcare", "exchange": "NYSE"},
    "MCK":   {"name": "McKesson Corp",                 "sector": "Healthcare", "exchange": "NYSE"},
    "CI":    {"name": "Cigna Group",                   "sector": "Healthcare", "exchange": "NYSE"},
    "HUM":   {"name": "Humana Inc",                    "sector": "Healthcare", "exchange": "NYSE"},
    "ELV":   {"name": "Elevance Health",               "sector": "Healthcare", "exchange": "NYSE"},
    "MOH":   {"name": "Molina Healthcare",             "sector": "Healthcare", "exchange": "NYSE"},
    "CNC":   {"name": "Centene Corp",                  "sector": "Healthcare", "exchange": "NYSE"},
    "TDOC":  {"name": "Teladoc Health",                "sector": "Healthcare", "exchange": "NYSE"},
    "ZBH":   {"name": "Zimmer Biomet Holdings",        "sector": "Healthcare", "exchange": "NYSE"},
    "BAX":   {"name": "Baxter International",          "sector": "Healthcare", "exchange": "NYSE"},
    "ALGN":  {"name": "Align Technology",              "sector": "Healthcare", "exchange": "NASDAQ"},
    "IDXX":  {"name": "IDEXX Laboratories",            "sector": "Healthcare", "exchange": "NASDAQ"},
    "MTD":   {"name": "Mettler-Toledo International",  "sector": "Healthcare", "exchange": "NYSE"},

    # ── Financials ────────────────────────────────────────────────────────────
    "JPM":   {"name": "JPMorgan Chase & Co",           "sector": "Financials", "exchange": "NYSE"},
    "BAC":   {"name": "Bank of America",               "sector": "Financials", "exchange": "NYSE"},
    "WFC":   {"name": "Wells Fargo & Co",              "sector": "Financials", "exchange": "NYSE"},
    "GS":    {"name": "Goldman Sachs Group",           "sector": "Financials", "exchange": "NYSE"},
    "MS":    {"name": "Morgan Stanley",                "sector": "Financials", "exchange": "NYSE"},
    "C":     {"name": "Citigroup Inc",                 "sector": "Financials", "exchange": "NYSE"},
    "BLK":   {"name": "BlackRock Inc",                 "sector": "Financials", "exchange": "NYSE"},
    "SCHW":  {"name": "Charles Schwab Corp",           "sector": "Financials", "exchange": "NYSE"},
    "AXP":   {"name": "American Express Co",           "sector": "Financials", "exchange": "NYSE"},
    "V":     {"name": "Visa Inc",                      "sector": "Financials", "exchange": "NYSE"},
    "MA":    {"name": "Mastercard Inc",                "sector": "Financials", "exchange": "NYSE"},
    "PYPL":  {"name": "PayPal Holdings",               "sector": "Financials", "exchange": "NASDAQ"},
    "SQ":    {"name": "Block Inc",                     "sector": "Financials", "exchange": "NYSE"},
    "FIS":   {"name": "Fidelity National Information", "sector": "Financials", "exchange": "NYSE"},
    "FISV":  {"name": "Fiserv Inc",                    "sector": "Financials", "exchange": "NASDAQ"},
    "GPN":   {"name": "Global Payments Inc",           "sector": "Financials", "exchange": "NYSE"},
    "BEN":   {"name": "Franklin Resources",            "sector": "Financials", "exchange": "NYSE"},
    "TROW":  {"name": "T. Rowe Price Group",           "sector": "Financials", "exchange": "NASDAQ"},
    "STT":   {"name": "State Street Corp",             "sector": "Financials", "exchange": "NYSE"},
    "BK":    {"name": "Bank of New York Mellon",       "sector": "Financials", "exchange": "NYSE"},
    "PNC":   {"name": "PNC Financial Services",        "sector": "Financials", "exchange": "NYSE"},
    "USB":   {"name": "U.S. Bancorp",                  "sector": "Financials", "exchange": "NYSE"},
    "TFC":   {"name": "Truist Financial Corp",         "sector": "Financials", "exchange": "NYSE"},
    "PRU":   {"name": "Prudential Financial",          "sector": "Financials", "exchange": "NYSE"},
    "MET":   {"name": "MetLife Inc",                   "sector": "Financials", "exchange": "NYSE"},
    "AFL":   {"name": "Aflac Inc",                     "sector": "Financials", "exchange": "NYSE"},
    "AIG":   {"name": "American International Group",  "sector": "Financials", "exchange": "NYSE"},
    "ALL":   {"name": "Allstate Corp",                 "sector": "Financials", "exchange": "NYSE"},
    "PGR":   {"name": "Progressive Corp",              "sector": "Financials", "exchange": "NYSE"},
    "TRV":   {"name": "Travelers Companies",           "sector": "Financials", "exchange": "NYSE"},
    "CB":    {"name": "Chubb Limited",                 "sector": "Financials", "exchange": "NYSE"},
    "ICE":   {"name": "Intercontinental Exchange",     "sector": "Financials", "exchange": "NYSE"},
    "CME":   {"name": "CME Group Inc",                 "sector": "Financials", "exchange": "NASDAQ"},
    "NDAQ":  {"name": "Nasdaq Inc",                    "sector": "Financials", "exchange": "NASDAQ"},
    "CBOE":  {"name": "Cboe Global Markets",           "sector": "Financials", "exchange": "CBOE"},
    "MSCI":  {"name": "MSCI Inc",                      "sector": "Financials", "exchange": "NYSE"},
    "SPGI":  {"name": "S&P Global Inc",                "sector": "Financials", "exchange": "NYSE"},
    "MCO":   {"name": "Moody's Corp",                  "sector": "Financials", "exchange": "NYSE"},
    "AFRM":  {"name": "Affirm Holdings",               "sector": "Financials", "exchange": "NASDAQ"},

    # ── Consumer Discretionary ────────────────────────────────────────────────
    "AMZN":  {"name": "Amazon.com Inc",                "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "TSLA":  {"name": "Tesla Inc",                     "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "HD":    {"name": "Home Depot Inc",                "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "MCD":   {"name": "McDonald's Corp",               "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "NKE":   {"name": "Nike Inc",                      "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "SBUX":  {"name": "Starbucks Corp",                "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "LOW":   {"name": "Lowe's Companies",              "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "TGT":   {"name": "Target Corp",                   "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "BKNG":  {"name": "Booking Holdings",              "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "MAR":   {"name": "Marriott International",        "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "HLT":   {"name": "Hilton Worldwide Holdings",     "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "YUM":   {"name": "Yum! Brands",                   "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "CMG":   {"name": "Chipotle Mexican Grill",        "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "DRI":   {"name": "Darden Restaurants",            "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "QSR":   {"name": "Restaurant Brands International","sector": "Consumer Discretionary", "exchange": "NYSE"},
    "WYNN":  {"name": "Wynn Resorts",                  "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "MGM":   {"name": "MGM Resorts International",     "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "LVS":   {"name": "Las Vegas Sands Corp",          "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "RCL":   {"name": "Royal Caribbean Group",         "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "CCL":   {"name": "Carnival Corp",                 "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "F":     {"name": "Ford Motor Co",                 "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "GM":    {"name": "General Motors Co",             "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "RIVN":  {"name": "Rivian Automotive",             "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "LCID":  {"name": "Lucid Group",                   "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "AZO":   {"name": "AutoZone Inc",                  "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "ORLY":  {"name": "O'Reilly Automotive",           "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "AN":    {"name": "AutoNation Inc",                "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "KMX":   {"name": "CarMax Inc",                    "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "BBY":   {"name": "Best Buy Co",                   "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "ETSY":  {"name": "Etsy Inc",                      "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "W":     {"name": "Wayfair Inc",                   "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "ABNB":  {"name": "Airbnb Inc",                    "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "UBER":  {"name": "Uber Technologies",             "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "LYFT":  {"name": "Lyft Inc",                      "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "LULU":  {"name": "Lululemon Athletica",           "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "DECK":  {"name": "Deckers Outdoor Corp",          "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "TPR":   {"name": "Tapestry Inc",                  "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "PVH":   {"name": "PVH Corp",                      "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "RL":    {"name": "Ralph Lauren Corp",             "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "VFC":   {"name": "VF Corp",                       "sector": "Consumer Discretionary", "exchange": "NYSE"},
    "HAS":   {"name": "Hasbro Inc",                    "sector": "Consumer Discretionary", "exchange": "NASDAQ"},
    "MAT":   {"name": "Mattel Inc",                    "sector": "Consumer Discretionary", "exchange": "NASDAQ"},

    # ── Consumer Staples ──────────────────────────────────────────────────────
    "PG":    {"name": "Procter & Gamble Co",           "sector": "Consumer Staples", "exchange": "NYSE"},
    "KO":    {"name": "Coca-Cola Co",                  "sector": "Consumer Staples", "exchange": "NYSE"},
    "PEP":   {"name": "PepsiCo Inc",                   "sector": "Consumer Staples", "exchange": "NASDAQ"},
    "WMT":   {"name": "Walmart Inc",                   "sector": "Consumer Staples", "exchange": "NYSE"},
    "COST":  {"name": "Costco Wholesale Corp",         "sector": "Consumer Staples", "exchange": "NASDAQ"},
    "MDLZ":  {"name": "Mondelez International",        "sector": "Consumer Staples", "exchange": "NASDAQ"},
    "GIS":   {"name": "General Mills Inc",             "sector": "Consumer Staples", "exchange": "NYSE"},
    "KHC":   {"name": "Kraft Heinz Co",                "sector": "Consumer Staples", "exchange": "NASDAQ"},
    "CAG":   {"name": "Conagra Brands",                "sector": "Consumer Staples", "exchange": "NYSE"},
    "HRL":   {"name": "Hormel Foods Corp",             "sector": "Consumer Staples", "exchange": "NYSE"},
    "CPB":   {"name": "Campbell Soup Co",              "sector": "Consumer Staples", "exchange": "NYSE"},
    "K":     {"name": "Kellanova (Kellogg's)",         "sector": "Consumer Staples", "exchange": "NYSE"},
    "MKC":   {"name": "McCormick & Co",                "sector": "Consumer Staples", "exchange": "NYSE"},
    "HSY":   {"name": "Hershey Co",                    "sector": "Consumer Staples", "exchange": "NYSE"},
    "MNST":  {"name": "Monster Beverage Corp",         "sector": "Consumer Staples", "exchange": "NASDAQ"},
    "KDP":   {"name": "Keurig Dr Pepper",              "sector": "Consumer Staples", "exchange": "NASDAQ"},
    "STZ":   {"name": "Constellation Brands",          "sector": "Consumer Staples", "exchange": "NYSE"},
    "PM":    {"name": "Philip Morris International",   "sector": "Consumer Staples", "exchange": "NYSE"},
    "MO":    {"name": "Altria Group",                  "sector": "Consumer Staples", "exchange": "NYSE"},
    "CLX":   {"name": "Clorox Co",                     "sector": "Consumer Staples", "exchange": "NYSE"},
    "CL":    {"name": "Colgate-Palmolive Co",          "sector": "Consumer Staples", "exchange": "NYSE"},
    "CHD":   {"name": "Church & Dwight Co",            "sector": "Consumer Staples", "exchange": "NYSE"},
    "EL":    {"name": "Estee Lauder Companies",        "sector": "Consumer Staples", "exchange": "NYSE"},
    "ULTA":  {"name": "Ulta Beauty Inc",               "sector": "Consumer Staples", "exchange": "NASDAQ"},
    "SYY":   {"name": "Sysco Corp",                    "sector": "Consumer Staples", "exchange": "NYSE"},
    "KR":    {"name": "Kroger Co",                     "sector": "Consumer Staples", "exchange": "NYSE"},
    "ACI":   {"name": "Albertsons Companies",          "sector": "Consumer Staples", "exchange": "NYSE"},
    "BJ":    {"name": "BJ's Wholesale Club",           "sector": "Consumer Staples", "exchange": "NYSE"},
    "DG":    {"name": "Dollar General Corp",           "sector": "Consumer Staples", "exchange": "NYSE"},
    "DLTR":  {"name": "Dollar Tree Inc",               "sector": "Consumer Staples", "exchange": "NASDAQ"},

    # ── Energy ────────────────────────────────────────────────────────────────
    "XOM":   {"name": "Exxon Mobil Corp",              "sector": "Energy", "exchange": "NYSE"},
    "CVX":   {"name": "Chevron Corp",                  "sector": "Energy", "exchange": "NYSE"},
    "COP":   {"name": "ConocoPhillips",                "sector": "Energy", "exchange": "NYSE"},
    "EOG":   {"name": "EOG Resources",                 "sector": "Energy", "exchange": "NYSE"},
    "SLB":   {"name": "SLB (Schlumberger)",            "sector": "Energy", "exchange": "NYSE"},
    "HAL":   {"name": "Halliburton Co",                "sector": "Energy", "exchange": "NYSE"},
    "BKR":   {"name": "Baker Hughes Co",               "sector": "Energy", "exchange": "NASDAQ"},
    "MPC":   {"name": "Marathon Petroleum Corp",       "sector": "Energy", "exchange": "NYSE"},
    "PSX":   {"name": "Phillips 66",                   "sector": "Energy", "exchange": "NYSE"},
    "VLO":   {"name": "Valero Energy Corp",            "sector": "Energy", "exchange": "NYSE"},
    "DVN":   {"name": "Devon Energy Corp",             "sector": "Energy", "exchange": "NYSE"},
    "FANG":  {"name": "Diamondback Energy",            "sector": "Energy", "exchange": "NASDAQ"},
    "APA":   {"name": "APA Corp",                      "sector": "Energy", "exchange": "NASDAQ"},
    "OXY":   {"name": "Occidental Petroleum",          "sector": "Energy", "exchange": "NYSE"},
    "MRO":   {"name": "Marathon Oil Corp",             "sector": "Energy", "exchange": "NYSE"},
    "CTRA":  {"name": "Coterra Energy",                "sector": "Energy", "exchange": "NYSE"},
    "KMI":   {"name": "Kinder Morgan",                 "sector": "Energy", "exchange": "NYSE"},
    "WMB":   {"name": "Williams Companies",            "sector": "Energy", "exchange": "NYSE"},
    "OKE":   {"name": "ONEOK Inc",                     "sector": "Energy", "exchange": "NYSE"},
    "TRGP":  {"name": "Targa Resources",               "sector": "Energy", "exchange": "NYSE"},
    "ENB":   {"name": "Enbridge Inc",                  "sector": "Energy", "exchange": "NYSE"},
    "TRP":   {"name": "TC Energy Corp",                "sector": "Energy", "exchange": "NYSE"},
    "ENPH":  {"name": "Enphase Energy",                "sector": "Energy", "exchange": "NASDAQ"},
    "FSLR":  {"name": "First Solar Inc",               "sector": "Energy", "exchange": "NASDAQ"},
    "BE":    {"name": "Bloom Energy Corp",             "sector": "Energy", "exchange": "NYSE"},
    "RUN":   {"name": "Sunrun Inc",                    "sector": "Energy", "exchange": "NASDAQ"},

    # ── Industrials ───────────────────────────────────────────────────────────
    "HON":   {"name": "Honeywell International",       "sector": "Industrials", "exchange": "NASDAQ"},
    "MMM":   {"name": "3M Co",                         "sector": "Industrials", "exchange": "NYSE"},
    "GE":    {"name": "GE Aerospace",                  "sector": "Industrials", "exchange": "NYSE"},
    "RTX":   {"name": "RTX Corp (Raytheon)",           "sector": "Industrials", "exchange": "NYSE"},
    "LMT":   {"name": "Lockheed Martin Corp",          "sector": "Industrials", "exchange": "NYSE"},
    "NOC":   {"name": "Northrop Grumman Corp",         "sector": "Industrials", "exchange": "NYSE"},
    "GD":    {"name": "General Dynamics Corp",         "sector": "Industrials", "exchange": "NYSE"},
    "BA":    {"name": "Boeing Co",                     "sector": "Industrials", "exchange": "NYSE"},
    "TDG":   {"name": "TransDigm Group",               "sector": "Industrials", "exchange": "NYSE"},
    "HWM":   {"name": "Howmet Aerospace",              "sector": "Industrials", "exchange": "NYSE"},
    "AXON":  {"name": "Axon Enterprise",               "sector": "Industrials", "exchange": "NASDAQ"},
    "IT":    {"name": "Gartner Inc",                   "sector": "Industrials", "exchange": "NYSE"},
    "ACN":   {"name": "Accenture PLC",                 "sector": "Industrials", "exchange": "NYSE"},
    "CTSH":  {"name": "Cognizant Technology Solutions","sector": "Industrials", "exchange": "NASDAQ"},
    "G":     {"name": "Genpact Limited",               "sector": "Industrials", "exchange": "NYSE"},
    "VRSK":  {"name": "Verisk Analytics",              "sector": "Industrials", "exchange": "NASDAQ"},
    "EFX":   {"name": "Equifax Inc",                   "sector": "Industrials", "exchange": "NYSE"},
    "MAS":   {"name": "Masco Corp",                    "sector": "Industrials", "exchange": "NYSE"},
    "SWK":   {"name": "Stanley Black & Decker",        "sector": "Industrials", "exchange": "NYSE"},
    "SNA":   {"name": "Snap-on Inc",                   "sector": "Industrials", "exchange": "NYSE"},
    "ITW":   {"name": "Illinois Tool Works",           "sector": "Industrials", "exchange": "NYSE"},
    "EMR":   {"name": "Emerson Electric Co",           "sector": "Industrials", "exchange": "NYSE"},
    "ETN":   {"name": "Eaton Corp PLC",                "sector": "Industrials", "exchange": "NYSE"},
    "ROK":   {"name": "Rockwell Automation",           "sector": "Industrials", "exchange": "NYSE"},
    "IR":    {"name": "Ingersoll Rand Inc",            "sector": "Industrials", "exchange": "NYSE"},
    "AME":   {"name": "AMETEK Inc",                    "sector": "Industrials", "exchange": "NYSE"},
    "PH":    {"name": "Parker Hannifin Corp",          "sector": "Industrials", "exchange": "NYSE"},
    "XYL":   {"name": "Xylem Inc",                     "sector": "Industrials", "exchange": "NYSE"},
    "CHRW":  {"name": "C.H. Robinson Worldwide",       "sector": "Industrials", "exchange": "NASDAQ"},
    "EXPD":  {"name": "Expeditors International",      "sector": "Industrials", "exchange": "NASDAQ"},
    "JBHT":  {"name": "J.B. Hunt Transport Services",  "sector": "Industrials", "exchange": "NASDAQ"},
    "ODFL":  {"name": "Old Dominion Freight Line",     "sector": "Industrials", "exchange": "NASDAQ"},
    "UNP":   {"name": "Union Pacific Corp",            "sector": "Industrials", "exchange": "NYSE"},
    "CSX":   {"name": "CSX Corp",                      "sector": "Industrials", "exchange": "NASDAQ"},
    "NSC":   {"name": "Norfolk Southern Corp",         "sector": "Industrials", "exchange": "NYSE"},
    "UPS":   {"name": "United Parcel Service",         "sector": "Industrials", "exchange": "NYSE"},
    "FDX":   {"name": "FedEx Corp",                    "sector": "Industrials", "exchange": "NYSE"},
    "GWW":   {"name": "W.W. Grainger Inc",             "sector": "Industrials", "exchange": "NYSE"},
    "URI":   {"name": "United Rentals Inc",            "sector": "Industrials", "exchange": "NYSE"},
    "FAST":  {"name": "Fastenal Co",                   "sector": "Industrials", "exchange": "NASDAQ"},
    "RSG":   {"name": "Republic Services Inc",         "sector": "Industrials", "exchange": "NYSE"},
    "WM":    {"name": "Waste Management Inc",          "sector": "Industrials", "exchange": "NYSE"},

    # ── Materials ─────────────────────────────────────────────────────────────
    "LIN":   {"name": "Linde PLC",                     "sector": "Materials", "exchange": "NASDAQ"},
    "APD":   {"name": "Air Products & Chemicals",      "sector": "Materials", "exchange": "NYSE"},
    "ECL":   {"name": "Ecolab Inc",                    "sector": "Materials", "exchange": "NYSE"},
    "SHW":   {"name": "Sherwin-Williams Co",           "sector": "Materials", "exchange": "NYSE"},
    "PPG":   {"name": "PPG Industries",                "sector": "Materials", "exchange": "NYSE"},
    "NEM":   {"name": "Newmont Corp",                  "sector": "Materials", "exchange": "NYSE"},
    "GOLD":  {"name": "Barrick Gold Corp",             "sector": "Materials", "exchange": "NYSE"},
    "AEM":   {"name": "Agnico Eagle Mines",            "sector": "Materials", "exchange": "NYSE"},
    "FNV":   {"name": "Franco-Nevada Corp",            "sector": "Materials", "exchange": "NYSE"},
    "WPM":   {"name": "Wheaton Precious Metals",       "sector": "Materials", "exchange": "NYSE"},
    "FCX":   {"name": "Freeport-McMoRan Inc",          "sector": "Materials", "exchange": "NYSE"},
    "SCCO":  {"name": "Southern Copper Corp",          "sector": "Materials", "exchange": "NYSE"},
    "MOS":   {"name": "Mosaic Co",                     "sector": "Materials", "exchange": "NYSE"},
    "NUE":   {"name": "Nucor Corp",                    "sector": "Materials", "exchange": "NYSE"},
    "STLD":  {"name": "Steel Dynamics Inc",            "sector": "Materials", "exchange": "NASDAQ"},
    "CLF":   {"name": "Cleveland-Cliffs Inc",          "sector": "Materials", "exchange": "NYSE"},
    "AA":    {"name": "Alcoa Corp",                    "sector": "Materials", "exchange": "NYSE"},
    "ALB":   {"name": "Albemarle Corp",                "sector": "Materials", "exchange": "NYSE"},
    "LTHM":  {"name": "Livent Corp",                   "sector": "Materials", "exchange": "NYSE"},
    "SQM":   {"name": "SQM (Sociedad Quimica y Minera)","sector": "Materials", "exchange": "NYSE"},
    "MP":    {"name": "MP Materials Corp",             "sector": "Materials", "exchange": "NYSE"},
    "VMC":   {"name": "Vulcan Materials Co",           "sector": "Materials", "exchange": "NYSE"},
    "MLM":   {"name": "Martin Marietta Materials",     "sector": "Materials", "exchange": "NYSE"},
    "PKG":   {"name": "Packaging Corp of America",     "sector": "Materials", "exchange": "NYSE"},
    "IP":    {"name": "International Paper Co",        "sector": "Materials", "exchange": "NYSE"},
    "WRK":   {"name": "WestRock Co",                   "sector": "Materials", "exchange": "NYSE"},

    # ── Real Estate ───────────────────────────────────────────────────────────
    "AMT":   {"name": "American Tower Corp (REIT)",    "sector": "Real Estate", "exchange": "NYSE"},
    "PLD":   {"name": "Prologis Inc (REIT)",           "sector": "Real Estate", "exchange": "NYSE"},
    "CCI":   {"name": "Crown Castle Inc (REIT)",       "sector": "Real Estate", "exchange": "NYSE"},
    "EQIX":  {"name": "Equinix Inc (REIT)",            "sector": "Real Estate", "exchange": "NASDAQ"},
    "DLR":   {"name": "Digital Realty Trust (REIT)",   "sector": "Real Estate", "exchange": "NYSE"},
    "SBAC":  {"name": "SBA Communications (REIT)",     "sector": "Real Estate", "exchange": "NASDAQ"},
    "VICI":  {"name": "VICI Properties (REIT)",        "sector": "Real Estate", "exchange": "NYSE"},
    "O":     {"name": "Realty Income Corp (REIT)",     "sector": "Real Estate", "exchange": "NYSE"},
    "SPG":   {"name": "Simon Property Group (REIT)",   "sector": "Real Estate", "exchange": "NYSE"},
    "AVB":   {"name": "AvalonBay Communities (REIT)",  "sector": "Real Estate", "exchange": "NYSE"},
    "EQR":   {"name": "Equity Residential (REIT)",     "sector": "Real Estate", "exchange": "NYSE"},
    "MAA":   {"name": "Mid-America Apartment (REIT)",  "sector": "Real Estate", "exchange": "NYSE"},
    "PSA":   {"name": "Public Storage (REIT)",         "sector": "Real Estate", "exchange": "NYSE"},
    "EXR":   {"name": "Extra Space Storage (REIT)",    "sector": "Real Estate", "exchange": "NYSE"},
    "NHI":   {"name": "National Health Investors (REIT)","sector": "Real Estate", "exchange": "NYSE"},
    "OHI":   {"name": "Omega Healthcare Investors",    "sector": "Real Estate", "exchange": "NYSE"},
    "WELL":  {"name": "Welltower Inc (REIT)",          "sector": "Real Estate", "exchange": "NYSE"},
    "VTR":   {"name": "Ventas Inc (REIT)",             "sector": "Real Estate", "exchange": "NYSE"},
    "PEAK":  {"name": "Healthpeak Properties (REIT)",  "sector": "Real Estate", "exchange": "NYSE"},
    "TRNO":  {"name": "Terreno Realty Corp (REIT)",    "sector": "Real Estate", "exchange": "NYSE"},
    "REXR":  {"name": "Rexford Industrial Realty",     "sector": "Real Estate", "exchange": "NYSE"},
    "EGP":   {"name": "EastGroup Properties (REIT)",   "sector": "Real Estate", "exchange": "NYSE"},
    "STAG":  {"name": "STAG Industrial Inc (REIT)",    "sector": "Real Estate", "exchange": "NYSE"},
    "COLD":  {"name": "Americold Realty Trust (REIT)", "sector": "Real Estate", "exchange": "NYSE"},

    # ── Utilities ─────────────────────────────────────────────────────────────
    "NEE":   {"name": "NextEra Energy Inc",            "sector": "Utilities", "exchange": "NYSE"},
    "DUK":   {"name": "Duke Energy Corp",              "sector": "Utilities", "exchange": "NYSE"},
    "SO":    {"name": "Southern Co",                   "sector": "Utilities", "exchange": "NYSE"},
    "AEP":   {"name": "American Electric Power",       "sector": "Utilities", "exchange": "NASDAQ"},
    "EXC":   {"name": "Exelon Corp",                   "sector": "Utilities", "exchange": "NASDAQ"},
    "PEG":   {"name": "Public Service Enterprise Group","sector": "Utilities", "exchange": "NYSE"},
    "SRE":   {"name": "Sempra Energy",                 "sector": "Utilities", "exchange": "NYSE"},
    "D":     {"name": "Dominion Energy Inc",           "sector": "Utilities", "exchange": "NYSE"},
    "PCG":   {"name": "PG&E Corp",                     "sector": "Utilities", "exchange": "NYSE"},
    "EIX":   {"name": "Edison International",          "sector": "Utilities", "exchange": "NYSE"},
    "ES":    {"name": "Eversource Energy",             "sector": "Utilities", "exchange": "NYSE"},
    "PPL":   {"name": "PPL Corp",                      "sector": "Utilities", "exchange": "NYSE"},
    "CMS":   {"name": "CMS Energy Corp",               "sector": "Utilities", "exchange": "NYSE"},
    "CNP":   {"name": "CenterPoint Energy",            "sector": "Utilities", "exchange": "NYSE"},
    "ATO":   {"name": "Atmos Energy Corp",             "sector": "Utilities", "exchange": "NYSE"},
    "WEC":   {"name": "WEC Energy Group",              "sector": "Utilities", "exchange": "NYSE"},
    "XEL":   {"name": "Xcel Energy Inc",               "sector": "Utilities", "exchange": "NASDAQ"},
    "AWK":   {"name": "American Water Works Co",       "sector": "Utilities", "exchange": "NYSE"},
    "IDA":   {"name": "IDACORP Inc",                   "sector": "Utilities", "exchange": "NYSE"},
    "POR":   {"name": "Portland General Electric",     "sector": "Utilities", "exchange": "NYSE"},
    "NI":    {"name": "NiSource Inc",                  "sector": "Utilities", "exchange": "NYSE"},
    "LNT":   {"name": "Alliant Energy Corp",           "sector": "Utilities", "exchange": "NASDAQ"},

    # ── Communication Services ────────────────────────────────────────────────
    "GOOGL": {"name": "Alphabet Inc (Google)",         "sector": "Communication Services", "exchange": "NASDAQ"},
    "META":  {"name": "Meta Platforms Inc",            "sector": "Communication Services", "exchange": "NASDAQ"},
    "NFLX":  {"name": "Netflix Inc",                   "sector": "Communication Services", "exchange": "NASDAQ"},
    "DIS":   {"name": "Walt Disney Co",                "sector": "Communication Services", "exchange": "NYSE"},
    "CMCSA": {"name": "Comcast Corp",                  "sector": "Communication Services", "exchange": "NASDAQ"},
    "CHTR":  {"name": "Charter Communications",        "sector": "Communication Services", "exchange": "NASDAQ"},
    "T":     {"name": "AT&T Inc",                      "sector": "Communication Services", "exchange": "NYSE"},
    "VZ":    {"name": "Verizon Communications",        "sector": "Communication Services", "exchange": "NYSE"},
    "TMUS":  {"name": "T-Mobile US Inc",               "sector": "Communication Services", "exchange": "NASDAQ"},
    "PARA":  {"name": "Paramount Global",              "sector": "Communication Services", "exchange": "NASDAQ"},
    "WBD":   {"name": "Warner Bros. Discovery",        "sector": "Communication Services", "exchange": "NASDAQ"},
    "LYV":   {"name": "Live Nation Entertainment",     "sector": "Communication Services", "exchange": "NYSE"},
    "TTWO":  {"name": "Take-Two Interactive Software", "sector": "Communication Services", "exchange": "NASDAQ"},
    "EA":    {"name": "Electronic Arts Inc",           "sector": "Communication Services", "exchange": "NASDAQ"},
    "RBLX":  {"name": "Roblox Corp",                   "sector": "Communication Services", "exchange": "NYSE"},
    "SNAP":  {"name": "Snap Inc",                      "sector": "Communication Services", "exchange": "NYSE"},
    "PINS":  {"name": "Pinterest Inc",                 "sector": "Communication Services", "exchange": "NYSE"},
    "MTCH":  {"name": "Match Group Inc",               "sector": "Communication Services", "exchange": "NASDAQ"},
    "IAC":   {"name": "IAC Inc",                       "sector": "Communication Services", "exchange": "NASDAQ"},
    "SPOT":  {"name": "Spotify Technology SA",         "sector": "Communication Services", "exchange": "NYSE"},
    "WIX":   {"name": "Wix.com Ltd",                   "sector": "Communication Services", "exchange": "NASDAQ"},
    "ZG":    {"name": "Zillow Group Inc",              "sector": "Communication Services", "exchange": "NASDAQ"},
    "OPEN":  {"name": "Opendoor Technologies",         "sector": "Communication Services", "exchange": "NASDAQ"},
    "DISH":  {"name": "DISH Network Corp",             "sector": "Communication Services", "exchange": "NASDAQ"},
    "LUMN":  {"name": "Lumen Technologies",            "sector": "Communication Services", "exchange": "NYSE"},
}

# Derived constants — computed once at import
SECTORS: list[str] = sorted({v["sector"] for v in _COMPANY_DB.values()})

# ── Public API ────────────────────────────────────────────────────────────────

def list_companies(
    sector: str | None = None,
    exchange: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """
    Return companies from the bundled DB, optionally filtered by sector/exchange.
    Each dict has: {ticker, name, sector, exchange}.
    Results are sorted alphabetically by ticker within each sector.
    """
    rows = [
        {"ticker": t, **v}
        for t, v in _COMPANY_DB.items()
        if (sector is None or v["sector"].lower() == sector.lower())
        and (exchange is None or v["exchange"].upper() == exchange.upper())
    ]
    rows.sort(key=lambda x: (x["sector"], x["ticker"]))
    return rows[:limit]


def search_companies(query: str, limit: int = 20) -> list[dict]:
    """
    Case-insensitive substring search on ticker + company name in the bundled DB.
    Returns up to `limit` matches, exact-ticker matches first.
    """
    q = query.upper().strip()
    exact, partial = [], []
    for ticker, v in _COMPANY_DB.items():
        if ticker == q:
            exact.append({"ticker": ticker, **v})
        elif q in ticker or q in v["name"].upper():
            partial.append({"ticker": ticker, **v})
    return (exact + partial)[:limit]


# ── EDGAR live search ─────────────────────────────────────────────────────────

_edgar_cache: list[dict] | None = None


def _load_edgar_universe() -> list[dict]:
    """Fetch and cache the SEC EDGAR company_tickers.json (~13K companies)."""
    global _edgar_cache
    if _edgar_cache is not None:
        return _edgar_cache
    r = requests.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers=_HEADERS,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    _edgar_cache = [
        {"ticker": e["ticker"], "name": e["title"], "cik": str(e["cik_str"])}
        for e in r.json().values()
        if e.get("ticker")
    ]
    return _edgar_cache


def search_edgar(query: str, limit: int = 50) -> list[dict]:
    """
    Live SEC EDGAR search across ~13,000 registered US public companies.
    Substring match on ticker and company name (case-insensitive).
    Returns [{ticker, name, cik}] — note: no sector/exchange data.
    Raises on network failure (caller should handle).
    """
    q = query.upper().strip()
    universe = _load_edgar_universe()
    exact, partial = [], []
    for entry in universe:
        t = entry["ticker"].upper()
        n = entry["name"].upper()
        if t == q:
            exact.append(entry)
        elif q in t or q in n:
            partial.append(entry)
    return (exact + partial)[:limit]

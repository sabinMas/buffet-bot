# Buffet-Bot

A terminal-based AI trading assistant that combines local LLMs, live market data, and Alpaca paper trading into a single CLI. Every decision is explained by the AI, all trades are paper-only by default, and no cloud AI subscription is required.

---

## What It Does

- **AI analysis** — two local LLMs (`deepseek-r1` + `qwen2.5:7b`) vote on BUY / SELL / HOLD
- **Live market data** — real-time quotes and news via Alpaca Data API, with yfinance fallback
- **Buffett fundamentals** — 100-point value score across ROE, ROIC, debt, margins, FCF, P/E, P/B, dividends
- **Dynamic position sizing** — ATR-based Kelly formula replaces flat LLM qty on every BUY
- **Backtesting** — RSI signal strategy vs. SPY buy-and-hold with Sharpe, drawdown, win rate
- **Correlation & sell signals** — portfolio correlation matrix, sector diversity, automated exit checks
- **Live terminal UI** — streaming price chart, candlestick PNG export, multi-ticker dashboard
- **Projections** — Monte Carlo simulation, what-if calculator, scenario comparison, milestone tracker
- **Paper trading only** — `paper=True` is hardcoded; no real money is ever at risk

---

## Prerequisites

| Requirement | Why |
|-------------|-----|
| Python 3.10+ | Runtime |
| [Ollama](https://ollama.com) | Runs LLMs locally — no cloud AI needed |
| Alpaca paper account | Free brokerage API for quotes, news, and paper trades |
| Git | To clone the repository |

---

## Setup: Step by Step

### 1. Get the Code

You do **not** need to fork the repository unless you plan to contribute back. A plain clone is enough:

```bash
git clone https://github.com/sabinMas/buffet-bot.git
cd buffet-bot
```

If you **do** want your own copy on GitHub (to push personal changes), fork first via the GitHub UI, then clone your fork:

```bash
git clone https://github.com/<your-username>/buffet-bot.git
cd buffet-bot
```

### 2. Create and Activate a Virtual Environment

```bash
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

# Windows (Git Bash / MINGW64)
python -m venv .venv
source .venv/Scripts/activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

This installs: `alpaca-py`, `yfinance`, `pandas`, `numpy`, `click`, `python-dotenv`, `ollama`, `requests`, `rich`, `plotext`, `mplfinance`.

> **Optional install check:** `pip install -e .` makes the `buffet-bot` command available globally in the venv.

### 4. Install and Configure Ollama

Ollama runs LLMs locally on your machine — no API key or subscription needed.

1. Download from [ollama.com](https://ollama.com) and install for your OS.
2. Start the Ollama server (it runs in the background automatically after install on most systems).
3. Pull the two required models:

```bash
ollama pull deepseek-r1
ollama pull qwen2.5:7b
```

These models are ~4–7 GB each. Pull time depends on your connection speed.

**Verify Ollama is running:**
```bash
ollama list
# Should show deepseek-r1 and qwen2.5:7b
```

### 5. Create an Alpaca Paper Trading Account

Alpaca is a commission-free brokerage with a free paper trading API.

1. Sign up at [alpaca.markets](https://alpaca.markets) — click **Get Started** and choose the free tier.
2. After signing in, navigate to **Paper Trading** in the left sidebar (not Live Trading).
3. In Paper Trading, go to **API Keys** → **Generate New Key**.
4. Copy your **API Key ID** and **Secret Key** — you only see the secret once.

> Paper trading is completely separate from any real money account. You start with $100,000 in simulated cash.

### 6. Create a `.env` File

In the root of the `buffet-bot` directory, create a file named `.env`:

```bash
# .env
ALPACA_API_KEY=PKXXXXXXXXXXXXXXXXXXXX
ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Replace the values with your actual keys from step 5. This file is gitignored and never committed.

### 7. Verify the Setup

```bash
python buffet-bot.py --help
```

You should see 21 commands listed. Then run a quick status check:

```bash
python buffet-bot.py status
```

This should print your paper account cash and buying power (e.g., `$100,000.00`).

---

## Running the Bot

### Analyze a Stock

```bash
# Standard Buffett-style analysis (dry run — no order placed)
python buffet-bot.py analyze AAPL

# High-risk mode adds RSI/MACD technicals
python buffet-bot.py analyze AAPL --risk high

# Use a different investment strategy lens
python buffet-bot.py analyze MSFT --strategy growth
python buffet-bot.py analyze KO --strategy dividend

# Execute a paper trade if consensus is BUY (prompts for confirmation)
python buffet-bot.py analyze AAPL --execute
```

### Buy a Stock

```bash
# Analyze then immediately prompt to buy
python buffet-bot.py buy TSLA --strategy turnaround
```

### Scan the Watchlist

```bash
# Scores AAPL, MSFT, GOOGL, BRK-B, JNJ, V, JPM, PG on Buffett criteria
python buffet-bot.py scan
```

### Account & Portfolio

```bash
python buffet-bot.py status        # cash and buying power
python buffet-bot.py history       # past paper orders
python buffet-bot.py portfolio     # equity chart over time
```

---

## Phase 2: Risk, Backtesting & Signals

### Backtest an RSI Strategy

```bash
# RSI<35+SMA50 BUY, RSI>70 or -7% SELL — compare against SPY buy-and-hold
python buffet-bot.py backtest AAPL --period 2 --capital 10000 --compare
```

Outputs: total return, CAGR, Sharpe ratio, max drawdown, win rate, profit factor, plus a plotext equity curve.

### Portfolio Correlation

```bash
# Correlation matrix of your open positions + sector diversity score
python buffet-bot.py correlate
```

Green = low correlation (<0.3), yellow = moderate, red = high (>0.6).

### Sell Signal Check

```bash
# Check for STOP (-7%), THESIS_BROKEN (score<40), UNDERPERFORM (bottom 20%), OVERBOUGHT (RSI>72)
python buffet-bot.py check-sells

# Actually execute paper sells for STOP / THESIS_BROKEN positions
python buffet-bot.py check-sells --execute
```

---

## Live Charts & Dashboard

### Stream Live Prices

```bash
# Rolling 60-tick price chart, refreshes every minute
python buffet-bot.py stream AAPL --interval 1m
# Press Ctrl+C to stop
```

### Candlestick Chart

```bash
# Terminal preview + save PNG candlestick with SMA(20,50)
python buffet-bot.py chart AAPL --period 1mo
python buffet-bot.py chart AAPL --period 5d --save my_chart.png
```

### Multi-Ticker Dashboard

```bash
# Refreshes every 60 seconds — Ctrl+C to exit
python buffet-bot.py dashboard AAPL MSFT GOOGL TSLA
```

---

## Projections & Planning

```bash
# Monte Carlo forecast of your current portfolio
python buffet-bot.py forecast --years 10 --monthly 500

# What-if calculator
python buffet-bot.py whatif --balance 10000 --monthly 500 --years 20 --ticker AAPL

# 5-scenario comparison (conservative → aggressive)
python buffet-bot.py scenarios --balance 10000 --monthly 500 --years 20

# Milestone tracker ($25k → $1M)
python buffet-bot.py milestones --balance 15000 --monthly 800 --return-pct 10
```

---

## Investment Guide & Plans

```bash
# Interactive wizard — analyze, build plans, paper trade
python buffet-bot.py guide

# Save a plan and re-run it later with fresh data
python buffet-bot.py plans
python buffet-bot.py plans --run my-plan
```

---

## AI Interaction

```bash
# Ask a one-off investing question
python buffet-bot.py ask "What is a good P/E ratio for a value stock?"

# Multi-turn chat with both AI models
python buffet-bot.py chat

# Look up a ticker by company name
python buffet-bot.py lookup "Apple"
```

---

## Architecture Overview

```
buffet-bot.py          ← entry point (runs buffet_bot/main.py)
buffet_bot/
  main.py              ← all logic: data, LLM, trading, commands (~2100 lines)
.env                   ← your Alpaca API keys (gitignored)
requirements.txt       ← Python dependencies
```

**Decision flow for `analyze`:**
1. Fetch 6-month price history + Buffett fundamentals via `yfinance`
2. Fetch live quote + today's bar from Alpaca Data API (yfinance fallback)
3. Fetch recent news from Alpaca News API → LLM sentiment scoring
4. Optionally add RSI/MACD (only with `--risk high`)
5. Query `deepseek-r1` + `qwen2.5:7b` simultaneously
6. Simple majority vote → consensus action
7. If BUY: ATR-based dynamic sizing panel
8. If `--execute` + BUY: prompt → paper market order via Alpaca

**Local LLM models (via Ollama):**
- `deepseek-r1` — primary reasoning model (default)
- `qwen2.5:7b` — consensus / secondary model

**Data sources:**
- `yfinance` — fundamentals, price history, RSI/MACD
- Alpaca Data API — real-time quotes, bars, news
- Alpaca Trading API — paper order submission, account info, portfolio history

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ValueError: Add ALPACA_API_KEY...` | Check `.env` exists in project root with correct keys |
| `ollama: command not found` | Install Ollama from [ollama.com](https://ollama.com) |
| LLM timeout / error | Run `ollama list` to confirm models are pulled; try `ollama run deepseek-r1` |
| `No data returned` for a ticker | Verify the symbol is correct (use `buffet-bot lookup <name>`) |
| Alpaca 403 / auth error | Regenerate API keys in the Alpaca dashboard (Paper Trading section) |
| `mplfinance not installed` | `pip install mplfinance` (optional, only needed for `chart` PNG export) |

---

## Security Note

This bot uses **Alpaca paper trading only** (`paper=True` is hardcoded in the source). It cannot place real trades. Your `.env` file is gitignored and should never be committed to version control.

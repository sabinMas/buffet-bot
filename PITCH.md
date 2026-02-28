# Buffet-Bot — Product Overview & Pitch

## The One-Line Version

> An AI-powered trading assistant that runs entirely on your machine, explains every decision, and never risks real money.

---

## The Problem It Solves

Retail investors face three brutal realities:

1. **Information overload** — thousands of metrics, earnings reports, and news items per day
2. **Emotion-driven decisions** — fear and greed override sound fundamentals
3. **Expensive tools** — Bloomberg terminals cost $25k/year; AI trading tools charge ongoing subscriptions

Most "AI trading" products are black boxes that give you a signal with no reasoning. You don't know *why* to buy — you just hope the model is right.

---

## What Buffet-Bot Does Differently

**Transparent AI reasoning.** Two local LLMs debate every trade out loud. You see both models' full JSON output — action, confidence, quantity, stop-loss, and a plain-English reason. The final decision is a majority vote.

**No subscription. No cloud.** Both AI models (`deepseek-r1` and `qwen2.5:7b`) run on your own hardware via [Ollama](https://ollama.com). Zero ongoing AI cost after setup.

**Paper trading by default.** `paper=True` is hardcoded. Every order goes to Alpaca's free paper trading environment — simulated money only. You can test strategies for months without touching a real dollar.

**Warren Buffett's framework, codified.** The scoring system awards points for ROE, ROIC, debt-to-equity, operating margins, free cash flow yield, P/E, P/B, and dividend yield — the same criteria Buffett has written about publicly for 40 years.

---

## Feature Overview

### Core Analysis Engine
| Feature | Detail |
|---------|--------|
| Buffett Score | 100-point value scoring across 6 fundamental criteria |
| Dual-LLM Consensus | `deepseek-r1` + `qwen2.5:7b` vote; majority wins |
| Live Market Data | Real-time quotes and bars via Alpaca Data API |
| News Sentiment | Recent headlines scored by the LLM for positive/neutral/negative impact |
| 4 Strategy Modes | Value, Growth, Dividend, Turnaround — each shifts the LLM's evaluation lens |

### Risk Management
| Feature | Detail |
|---------|--------|
| Dynamic Position Sizing | ATR-based formula replaces flat LLM-suggested quantity on every BUY |
| Sell Signal Checker | Flags STOP (−7%), Thesis Broken (score<40), Underperformers, Overbought (RSI>72) |
| Auto-Execute Sells | Optional `--execute` flag places paper market sell orders for flagged positions |

### Backtesting
| Feature | Detail |
|---------|--------|
| RSI Strategy Backtest | BUY when RSI<35 + price>SMA50; SELL when RSI>70 or −7% from entry |
| SPY Benchmark | Automatic buy-and-hold SPY comparison column |
| Full Metrics Suite | Total return, CAGR, Sharpe ratio, max drawdown, win rate, profit factor, trade count |
| Equity Curve Chart | Plotext terminal chart of the strategy's portfolio value over time |

### Portfolio Analytics
| Feature | Detail |
|---------|--------|
| Correlation Matrix | Color-coded pairwise correlation of all holdings (green/yellow/red) |
| Diversity Score | 0–1 score (1.0 = fully uncorrelated portfolio) |
| Sector Breakdown | Counter-based sector distribution with concentration warnings |

### Live Visualization
| Feature | Detail |
|---------|--------|
| `stream` | Rolling 60-tick price chart in the terminal, refreshes every 1/5/15 minutes |
| `chart` | Terminal SMA overlay + mplfinance candlestick PNG export |
| `dashboard` | Multi-ticker live table refreshing every 60 seconds |

### Projections & Planning
| Feature | Detail |
|---------|--------|
| Monte Carlo | 1,000 simulated paths; P10/median/P90 probability cone |
| What-If Calculator | Model any balance + contribution + return rate scenario |
| Scenario Comparison | Conservative / AI Balanced / Aggressive / Bear / S&P 500 side-by-side |
| Milestone Tracker | Years to $25k, $50k, $100k, $250k, $500k, $1M |
| Investment Plans | Save multi-stock plans with budgets and re-run them with fresh data |

### AI Utilities
| Feature | Detail |
|---------|--------|
| Free-form Q&A | Ask any investing question; both models answer |
| Multi-turn Chat | Full conversation history with each model in an REPL loop |
| Company Lookup | Search ticker by company name via yfinance |
| Guided Wizard | Step-by-step mode for beginners: analyze → plan → execute |

---

## Command Reference (21 commands)

```
Core Trading
  analyze       Full analysis + optional paper buy
  buy           Analyze then immediately prompt to buy
  scan          Score a fixed watchlist on Buffett criteria

Portfolio
  status        Account cash and buying power
  history       Past paper orders with fill prices
  portfolio     Equity curve chart over time

Phase 2: Risk & Signals
  backtest      RSI strategy backtest vs. SPY
  correlate     Correlation matrix + sector diversity
  check-sells   Sell signal audit (+ --execute flag)

Live Charts
  stream        Rolling price stream with chart
  chart         Terminal preview + candlestick PNG
  dashboard     Multi-ticker live dashboard

Projections
  forecast      Monte Carlo forecast of current holdings
  whatif        Interactive what-if calculator
  scenarios     5-scenario comparison table
  milestones    Years to hit $25k–$1M

AI & Planning
  ask           One-shot investing question
  chat          Multi-turn conversation with both models
  lookup        Find ticker by company name
  guide         Interactive wizard (beginner-friendly)
  plans         Save, list, run, delete investment plans
```

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| CLI framework | [Click](https://click.palletsprojects.com) |
| Terminal UI | [Rich](https://rich.readthedocs.io) |
| Terminal charts | [Plotext](https://github.com/piccolomo/plotext) |
| Candlestick charts | [mplfinance](https://github.com/matplotlib/mplfinance) |
| Local AI | [Ollama](https://ollama.com) (`deepseek-r1`, `qwen2.5:7b`) |
| Market data | [yfinance](https://github.com/ranaroussi/yfinance), Alpaca Data API |
| Paper trading | [Alpaca Markets](https://alpaca.markets) (paper=True, hardcoded) |
| Numerics | NumPy, Pandas |
| Persistence | SQLite (`~/.buffet-bot.db`) — logs every BUY recommendation |

---

## Who This Is For

**Individual investors** who want AI-assisted analysis without paying for Bloomberg or a cloud AI subscription.

**Finance students** learning to backtest strategies, understand risk metrics, and apply fundamental analysis — with working code they can modify.

**Small teams / startups** evaluating algorithmic trading ideas before committing engineering resources to a full trading system.

**Developers** who want to self-host their AI trading workflow with no dependency on third-party AI APIs.

---

## What It Is Not

- Not a licensed financial advisor or investment service
- Not connected to live trading (paper=True is hardcoded; a deliberate safety constraint)
- Not a black box — every model output is printed in full before any order is placed

---

## Potential Extensions

The architecture is a single Python file with a clean section structure. Easy areas to extend:

- Swap in any Ollama-compatible model (Llama 3, Mistral, Phi-3, etc.)
- Add new commands by decorating a function with `@cli.command()`
- Replace paper trading with live Alpaca trading by removing the `paper=True` flag (use with extreme caution)
- Add a watchlist scanner with email/Slack alerts
- Export backtest results to CSV for further analysis in Excel or Jupyter

---

## Getting Started in Under 10 Minutes

```bash
git clone https://github.com/sabinMas/buffet-bot.git
cd buffet-bot
pip install -r requirements.txt
# Add ALPACA_API_KEY and ALPACA_SECRET_KEY to .env
# Pull Ollama models: ollama pull deepseek-r1 && ollama pull qwen2.5:7b
python buffet-bot.py analyze AAPL
```

Full setup guide: see **README.md**

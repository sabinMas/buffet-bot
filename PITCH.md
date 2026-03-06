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

**Paper trading by default.** `paper=True` is the default safe mode. Live trading requires `BUFFET_BOT_LIVE=true` env var plus a triple-confirmation prompt per trade — a deliberate two-factor safety gate.

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

### Live Trading & Compounding Engine (v0.6)
| Feature | Detail |
|---------|--------|
| `compound` Command | Reinvests dividends + realized profits into top-ranked Buffett-scored positions |
| `automate --sweep` | Fully autonomous scan → analyze → size → execute pipeline; deterministic and auditable |
| SPY Benchmark Overlay | `portfolio` gains CAGR / alpha / Sharpe vs buy-and-hold SPY |

### Multi-Factor Edge Intelligence (v0.7)
| Feature | Detail |
|---------|--------|
| EDGE_SCORE Formula | Weighted blend: Buffett (30%) + LLM (20%) + Insider (20%) + Politician (10%) + Earnings (10%) + Analyst (10%) |
| `edge-scan` Command | Ranks universe by EDGE_SCORE; `--min-edge`, `--top`, `--weights`, `--json` flags |
| Historical Edge Backtest | `backtest --edge`: weekly-rebalanced EDGE portfolio vs SPY; strict no-lookahead bias |

### Options Income Engine (v0.8)
| Feature | Detail |
|---------|--------|
| Covered Calls | Finds optimal 0.30-delta, 21–45 DTE contracts on existing positions |
| Cash-Secured Puts | Targets tickers with EDGE_SCORE > 65; validates cash requirement |
| Income Dashboard | Open positions, DTE countdown, P&L, 12-month income bar chart (plotext) |
| Roll Check | 7-DTE flag; ATR-based risk assessment for rolling vs closing |

### Macro & Sector Intelligence (v0.9)
| Feature | Detail |
|---------|--------|
| FRED Regime Detector | Classifies macro regime from yield spread, PMI, unemployment; 1-hour cache |
| `sectors` Command | 11 GICS ETF momentum ranking (30d/90d/1y weighted); plotext bar chart |
| `rotation-check` | Current vs target allocation by regime; rotation matrix; `--execute` queues trades |
| `hedge` | Beta-adjusted SPY put sizing for portfolio protection (display-only by default) |

---

## The 10X Compounding Framework

Buffet-Bot's roadmap is designed around four stacked income and growth layers that compound on each other:

| Layer | Mechanism | Target CAGR Contribution |
|-------|-----------|--------------------------|
| 1 | Multi-Factor EDGE_SCORE (Buffett + LLM + Insider + Politician + Earnings + Analyst) | +12–18% alpha over SPY |
| 2 | Options Income (covered calls + cash-secured puts at 1–2%/mo) | +15–20% annualized yield |
| 3 | Automated Compounding (daily dividend/premium reinvestment, zero cash drag) | +2–3% |
| 4 | Macro Regime Timing (avoid 20–40% drawdowns via sector rotation) | +5–8% risk-adjusted |

**Illustrative compounding math** — $10,000 start, $500/month contributions:

| Scenario | 3-Year FV | 5-Year FV |
|----------|-----------|-----------|
| S&P 500 baseline (10%) | $32,480 | $48,866 |
| Equity alpha only (25%) | $46,223 | $93,111 |
| + Options income (40%) | $62,841 | $179,204 |
| + Full compounding engine (42%) | $66,390 | $194,550 |

*Illustrative only — not financial advice. Past performance does not guarantee future results. All figures assume reinvestment of all income and no taxes or fees.*

---

## Command Reference (35+ commands)

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

Live Trading & Compounding (v0.6):  compound, automate --sweep
Edge Intelligence (v0.7):          edge-scan
Options Income (v0.8):             options-income (covered-calls, cash-puts, dashboard, roll-check)
Macro Intelligence (v0.9):         sectors, rotation-check, hedge
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

## Roadmap Preview

| Version | Theme | Key Addition |
|---------|-------|--------------|
| v0.6 | Live Trading & Compounding | `compound` + `automate --sweep`; live guard triple-confirmation; SPY benchmark overlay |
| v0.7 | Multi-Factor Edge Intelligence | `EDGE_SCORE` combining 6 signal sources; `edge-scan`; edge-vs-SPY backtest |
| v0.8 | Options Income Engine | Covered calls + cash-secured puts screener; income dashboard; roll checker |
| v0.9 | Sector Rotation & Macro | FRED regime detector; `sectors` momentum ranking; `rotation-check`; `hedge` |
| v1.0 | Production-Grade CLI | ML signal weight tuning; PyPI package; Docker + Ollama sidecar; full CHANGELOG |

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

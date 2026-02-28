# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

Requires a `.env` file with Alpaca paper trading credentials:
```
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
```

Install dependencies:
```bash
pip install -r requirements.txt
```

Requires [Ollama](https://ollama.com) running locally with the models pulled:
```bash
ollama pull deepseek-r1
ollama pull qwen2.5:7b
```

## Running the Bot

```bash
# Analyze a stock (dry-run by default)
python buffet-bot.py analyze AAPL
python buffet-bot.py analyze AAPL --risk high --model qwen2.5:7b

# Execute a real paper trade (requires --execute flag + confirmation prompt)
python buffet-bot.py analyze AAPL --execute

# Scan a fixed watchlist for top Buffett scores
python buffet-bot.py scan

# Check paper account cash and buying power
python buffet-bot.py status
```

## Architecture

Single-file CLI app (`buffet-bot.py`) built with Click. All trading uses Alpaca's **paper trading** API only (`paper=True` is hardcoded).

**Decision flow for `analyze`:**
1. Fetch 6-month price history and Buffett fundamentals (ROE, debt/equity, operating margin) via `yfinance`
2. Optionally fetch RSI/MACD technicals (only when `--risk high`)
3. Query local LLMs via Ollama — always queries the primary model plus `qwen2.5:7b` as a secondary model (unless the primary is already `qwen2.5:7b`)
4. Each model returns a JSON blob: `{action, confidence, qty, reason, stop_pct}`
5. Simple majority vote across model responses determines consensus action
6. If `--execute` and consensus is BUY, prompts for confirmation before placing a market order

**Buffett scoring** (`get_buffett_metrics`): 100-point score — ROE >15% (+40), Debt/Equity <50 (+30), Operating Margin >10% (+30).

**LLM models** (`MODELS` list): `deepseek-r1` (default primary), `qwen2.5:7b` (secondary/consensus). Both must be available in the local Ollama instance.

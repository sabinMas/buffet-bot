"""Global constants, API clients, and configuration for buffet-bot."""
import os
import json
import pathlib
import click
import contextlib
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3
import math
import itertools
import numpy as np
from collections import Counter, deque
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockLatestBarRequest
import yfinance as yf
import pandas as pd
import ollama
import time
import warnings
warnings.filterwarnings('ignore')

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.text import Text
from rich import box
import plotext as plt

load_dotenv()

API_KEY = os.getenv('ALPACA_API_KEY')
SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
if not API_KEY or not SECRET_KEY:
    raise ValueError("Add ALPACA_API_KEY and ALPACA_SECRET_KEY to .env")

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
console = Console()

MODELS = ['deepseek-r1', 'qwen2.5:7b']
ALPACA_PAPER_BASE = 'https://paper-api.alpaca.markets'

MODEL_COLORS = {
    'deepseek-r1': 'cyan',
    'qwen2.5:7b': 'magenta',
}

PLANS_DIR   = os.path.expanduser("~/.buffet-plans")
DB_PATH     = os.path.expanduser("~/.buffet-bot.db")
CONFIG_PATH = os.path.expanduser("~/.buffet-bot-config.toml")

FRED_API_KEY = os.getenv('FRED_API_KEY', '')

NASDAQ_EARNINGS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin":     "https://www.nasdaq.com",
    "Referer":    "https://www.nasdaq.com/",
    "Accept":     "application/json, text/plain, */*",
}

STRATEGY_PROMPTS = {
    'value': (
        "Focus on Warren Buffett's classic value principles: high ROE, low debt, "
        "durable competitive moat, P/E and P/B below sector median. "
        "Require a 30%+ margin of safety from intrinsic value. Penalize P/E > 25 or P/B > 5."
    ),
    'growth': (
        "Focus on high-growth characteristics: 5Y earnings CAGR > 15%, expanding margins, "
        "strong revenue momentum. Accept higher P/E if justified by growth rate (PEG < 1.5). "
        "Prioritize total addressable market size and reinvestment rate."
    ),
    'dividend': (
        "Focus on income generation: consistent dividend yield > 2.5%, payout ratio < 60%, "
        "10+ years of dividend growth. Prioritize FCF coverage of dividends and debt stability. "
        "Flag dividend traps where yield > 8% with declining earnings."
    ),
    'turnaround': (
        "Focus on distressed assets with recovery catalysts: recent earnings trajectory improvement, "
        "debt reduction trend, new management, or sector tailwind. Accept recent losses if the "
        "fundamental thesis is clearly improving. High risk — require a strong conviction catalyst."
    ),
}

GOAL_PRESETS = {
    'growth':   ['AAPL', 'MSFT', 'GOOGL', 'NVDA', 'AMZN'],
    'income':   ['JNJ', 'PG', 'KO', 'VZ', 'ABBV'],
    'balanced': ['V', 'JPM', 'BRK-B', 'SPY', 'QQQ'],
    'etf':      ['SPY', 'QQQ', 'VTI', 'SCHD', 'AGG'],
    'buffett':  ['BRK-B', 'KO', 'AAPL', 'JNJ', 'V'],
}

# ── Config ────────────────────────────────────────────────────────────────────

try:
    import tomllib
except ImportError:
    tomllib = None  # Python < 3.11 — config reads silently disabled

try:
    import tomli_w
except ImportError:
    tomli_w = None

_CONFIG_DEFAULTS = {
    'defaults': {'model': MODELS[0], 'risk': 'medium', 'strategy': 'value'},
    'display':  {'buffett_score_green': 70, 'buffett_score_yellow': 40},
}

def _load_config():
    """Load ~/.buffet-bot-config.toml, merging with hardcoded defaults."""
    cfg = {s: dict(v) for s, v in _CONFIG_DEFAULTS.items()}
    if tomllib is None or not os.path.exists(CONFIG_PATH):
        return cfg
    try:
        with open(CONFIG_PATH, 'rb') as f:
            user = tomllib.load(f)
        for section, values in user.items():
            if section in cfg and isinstance(values, dict):
                cfg[section].update(values)
    except Exception:
        pass
    return cfg

_CONFIG = _load_config()

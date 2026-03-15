"""Global constants, API clients, and configuration for buffet-bot."""
import os
import json
import pathlib
import subprocess
import contextlib
import requests as _req
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.data import StockHistoricalDataClient
import time
import warnings
warnings.filterwarnings('ignore')

from rich.console import Console
from rich.panel import Panel
from rich import box

load_dotenv()

API_KEY = os.getenv('ALPACA_API_KEY')
SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
if not API_KEY or not SECRET_KEY:
    raise ValueError("Add ALPACA_API_KEY and ALPACA_SECRET_KEY to .env")

data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
console = Console()

MODELS = ['deepseek-r1', 'qwen2.5:7b']
ALPACA_PAPER_BASE = 'https://paper-api.alpaca.markets'

MODEL_COLORS = {
    'deepseek-r1': 'cyan',
    'qwen2.5:7b': 'magenta',
}


def ensure_ollama_running() -> bool:
    """Start Ollama automatically if it is not already running.

    Returns True if Ollama is reachable, False if it could not be started.
    """
    try:
        _req.get("http://localhost:11434", timeout=2)
        return True  # already up
    except Exception:
        pass

    console.print("[dim]Ollama not detected — starting it in the background...[/dim]")
    try:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if hasattr(subprocess, "DETACHED_PROCESS"):  # Windows only
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        subprocess.Popen(["ollama", "serve"], **kwargs)
    except FileNotFoundError:
        console.print(Panel(
            "[bold red]Ollama is not installed or not on PATH.[/bold red]\n\n"
            "Install it from [cyan]https://ollama.com[/cyan] and pull the required models:\n"
            "  [cyan]ollama pull deepseek-r1[/cyan]\n"
            "  [cyan]ollama pull qwen2.5:7b[/cyan]",
            title="[bold red]Ollama Not Found[/bold red]",
            border_style="red",
        ))
        return False
    except Exception as e:
        console.print(f"[red]Could not start Ollama: {e}[/red]")
        return False

    # Poll until Ollama responds — exponential backoff (0.2s, 0.4s, 0.8s, …, cap 2s)
    delay = 0.2
    for _ in range(10):
        time.sleep(delay)
        delay = min(delay * 2, 2.0)
        try:
            _req.get("http://localhost:11434", timeout=1)
            console.print("[dim green]Ollama started successfully.[/dim green]")
            return True
        except Exception:
            pass

    console.print(Panel(
        "[bold red]Ollama did not start in time.[/bold red]\n\n"
        "Try starting it manually:\n"
        "  [cyan]ollama serve[/cyan]",
        title="[bold red]Ollama Timeout[/bold red]",
        border_style="red",
    ))
    return False


PLANS_DIR   = os.path.expanduser("~/.buffet-plans")
DB_PATH     = os.path.expanduser("~/.buffet-bot.db")
CONFIG_PATH = os.path.expanduser("~/.buffet-bot-config.toml")

# Live guard — imported after DB_PATH and console are defined to avoid circular
# import issues (live_guard.py imports DB_PATH and console from this module).
from buffet_bot.live_guard import is_live_mode, get_trading_client as _get_trading_client
LIVE_MODE = is_live_mode()
trading_client = _get_trading_client()

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
    'speculative': (
        "Focus on high-risk, high-reward momentum plays: small-cap tickers with high beta, "
        "elevated short interest, recent catalysts, or meme momentum. Accept weak fundamentals "
        "if price momentum, squeeze potential, or a near-term catalyst is compelling. "
        "Use tight stop-losses (5–8%) and limit each position to 1–3% of portfolio. "
        "This is speculation — expect higher loss rates in exchange for outsized upside."
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
    'edge': {
        'min_score': 60,
        'buffett_weight':    0.30,
        'llm_weight':        0.20,
        'insider_weight':    0.20,
        'politician_weight': 0.10,
        'earnings_weight':   0.10,
        'analyst_weight':    0.10,
    },
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

# ── Theme ─────────────────────────────────────────────────────────────────────
# Set BUFFET_BOT_THEME=light in your environment for a light-background terminal.
# Default is 'dark' (bright colors optimised for dark terminals).

_THEME_MODE = os.getenv('BUFFET_BOT_THEME', 'dark').strip().lower()
if _THEME_MODE not in ('dark', 'light'):
    _THEME_MODE = 'dark'

_THEMES = {
    'dark': {
        'primary':   'bright_cyan',
        'secondary': 'bright_magenta',
        'success':   'bright_green',
        'warning':   'bright_yellow',
        'danger':    'bright_red',
        'muted':     'dim',
        'header':    'bold bright_cyan',
        'border':    'bright_cyan',
        'value':     'bright_white',
    },
    'light': {
        'primary':   'blue',
        'secondary': 'dark_magenta',
        'success':   'green',
        'warning':   'dark_goldenrod',
        'danger':    'red',
        'muted':     'dim',
        'header':    'bold blue',
        'border':    'blue',
        'value':     'black',
    },
}

THEME = _THEMES[_THEME_MODE]


def theme_color(role: str) -> str:
    """Return the Rich color string for a semantic role in the active theme.

    Roles: primary, secondary, success, warning, danger, muted, header, border, value.
    Falls back to 'white' for unknown roles.

    Example:
        console.print(Panel("hello", border_style=theme_color('border')))
    """
    return THEME.get(role, 'white')

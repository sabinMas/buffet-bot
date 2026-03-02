# UI Audit & Styling Roadmap — Buffet-Bot

**Session**: March 2026
**Goal**: Transform buffet-bot CLI from functional to vibrant, engaging, premium trading terminal aesthetic

---

## Current State Analysis

### Display Principles (Existing)
- ✓ Uses Rich library for all output (`console.print()`)
- ✓ Basic color coding: green (BUY), red (SELL), yellow (HOLD)
- ✓ Model colors defined: cyan (deepseek-r1), magenta (qwen2.5:7b)
- ✓ Panels use `border_style` parameter consistently
- ✗ Limited use of Rich's advanced features (gradients, custom borders, styled text)
- ✗ Panel title styling is inconsistent (some bold, some plain)
- ✗ Table header colors vary across commands
- ✗ No use of progress spinners for long operations
- ✗ Spacing and rhythm could be more dramatic

### Current Panel Styles
- `border_style="blue"` for headers/analyzing — muted, corporate feel
- `border_style="cyan"` for position sizing, portfolio summaries
- `border_style="green"` for live market data
- `border_style="yellow"` for warnings/earnings alerts
- No use of `box.HEAVY`, `box.DOUBLE`, `box.ROUNDED` for visual hierarchy

### Current Table Styles
- `header_style="bold blue"` (scan, browse, rebalance)
- `header_style="bold cyan"` (some rebalance, correlate tables)
- Inconsistent column styling (some dim, some bold, some plain)
- Limited color-coding of values (Buffett scores use `_score_color`, others use plain text)

### Commands Audited
**Trading**: ask, lookup, browse, analyze, buy, history, portfolio, chat, scan, status, stream, chart, dashboard
**Portfolio**: rebalance, backtest, correlate, check_sells, var, forecast, whatif, scenarios, milestones
**Intel**: news, insiders, crypto, volatile, options
**Account**: guide, plans, automate, config, alerts, watchlist, beats, completion

---

## Design System Update

### Color Palette (Enhanced)
| Semantic Meaning | Current | Enhanced | Usage |
|-----------------|---------|----------|-------|
| BUY / Bullish | green | `bold bright_green` | Actions, positive signals, gains |
| SELL / Bearish | red | `bold bright_red` | Warnings, sell signals, losses |
| HOLD / Neutral | yellow | `bold bright_yellow` | Neutral actions, cautions |
| Info / Headers | cyan | `bold bright_cyan` | Primary headers, key info panels |
| Success Panel | green | `bold green` on dark (if supported) | Execution confirmations |
| Error / Critical | bold red | `bold bright_red` | Error states, critical alerts |
| Metrics / Data | white | `bold white` for emphasis, `dim white` for secondary | Scores, numbers, secondary text |
| Primary Model (deepseek-r1) | cyan | `bold cyan` | Model response panels, emphasis |
| Secondary Model (qwen2.5:7b) | magenta | `bold magenta` | Secondary model responses |
| Consensus (context) | dim | `dim cyan` or `dim white` | Supplementary info |
| Links / Tips | blue | `bold bright_blue` | Helpful hints, navigation |

### Border Style Hierarchy
- **Primary headers/main analysis**: `box.ROUNDED` with `border_style="bright_cyan"`
- **Status/account panels**: `box.ROUNDED` with `border_style="bright_green"` (for positive) or `border_style="bright_yellow"` (for neutral)
- **Warnings/alerts**: `box.ROUNDED` with `border_style="bright_red"`
- **Secondary data**: `box.SIMPLE` with `border_style="dim cyan"` (less visual weight)
- **Model responses**: `box.ROUNDED` with model's color (`bright_cyan` or `bright_magenta`)

### Panel Title Styling
- All titles: `[bold bright_<color>]Title[/bold bright_<color>]`
- Use of subtitle patterns: "Ticker (Context) | Detail"
- Examples:
  - `[bold bright_cyan]Analyzing AAPL[/bold bright_cyan]`
  - `[bold bright_green]Buffett Scan Results[/bold bright_green]`
  - `[bold bright_yellow]WARNING: Earnings Alert[/bold bright_yellow]`

### Table Design
- **Headers**: `header_style="bold bright_cyan"` (primary), `header_style="bold bright_yellow"` (secondary)
- **Score columns**: Color-code values inline using `_score_color()` helper
- **Price/value columns**: Use change direction colors (green/red)
- **Action columns**: Use semantic colors (green=BUY/ADD, red=SELL/TRIM, yellow=HOLD)
- **Numeric columns**: Right-justified, bold for important values
- **Text columns**: Left-justified, dim for secondary info

---

## Styling Improvements by Category

### 1. Core Display Helpers (`display.py`)
**Changes**:
- ✓ `_print_ai_responses()`: Use `box.ROUNDED`, brighten model colors
- ✓ `_print_live_market()`: Add "Live Snapshot" header, brighten green, add icon/emoji accent
- ✓ Update `_consensus_text()` to use `bright_` variants
- ✓ Add `_make_panel_title()` helper for consistent title formatting
- ✓ Add `_styled_section_header()` for section breaks (cyan dividers)

### 2. Trading Commands (`cmd_trading.py`)
**Changes**:
- ✓ `analyze`: Update "Analyzing" header panel to use bright cyan rounded box
- ✓ `analyze`: Add spinners/progress for LLM queries
- ✓ `scan`: Change table header to `bright_cyan`, color-code scores
- ✓ `scan`: Add visual banner above results
- ✓ `status`: Redesign with brighter colors, better layout
- ✓ `buy`: Match analyze styling
- ✓ `history`: Add color-coding for order status (filled=green, open=yellow)
- ✓ `portfolio`: Update title and styling
- ✓ `chat`: Enhance panel styling

### 3. Portfolio Commands (`cmd_portfolio.py`)
**Changes**:
- ✓ `rebalance`: Upgrade table to bright colors, highlight ADD/TRIM actions
- ✓ `backtest`: Add visual result summary with color-coded metrics
- ✓ `correlate`: Color matrix by correlation strength (red=high, yellow=medium, green=low)
- ✓ `forecast`: Enhanced Monte Carlo visualization
- ✓ `whatif`: Better interactive prompts
- ✓ `scenarios`: Color-code return scenarios

### 4. Intel Commands (`cmd_intel.py`)
**Changes**:
- ✓ Update all table headers to `bright_cyan`
- ✓ Add emotion coloring to news sentiment (red=negative, yellow=neutral, green=positive)
- ✓ Enhance insider trading display with volume highlights

### 5. Account Commands (`cmd_account.py`)
**Changes**:
- ✓ Update status panels
- ✓ Enhance alert/watchlist displays
- ✓ Better config feedback styling

---

## Technical Notes

### Rich Components to Leverage
```python
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn

# Advanced features to use:
# 1. Text objects with multiple styles: Text("value", style="bold bright_green")
# 2. Custom progress spinners for LLM queries
# 3. Tables with row-level styling via Panel nesting
# 4. box.HEAVY, box.DOUBLE for visual hierarchy
# 5. Gradient-like effects via consistent color themes
```

### Helper Functions to Add
```python
def _make_panel_title(text, color='bright_cyan', icon=None):
    """Consistent panel title formatting."""
    icon_str = f"{icon} " if icon else ""
    return f"[bold {color}]{icon_str}{text}[/bold {color}]"

def _styled_section_header(text, color='bright_cyan'):
    """Section separator with color."""
    return Panel(text, border_style=color, expand=False, padding=(0, 2))
```

---

## Implementation Order
1. **display.py**: Core helpers and styling functions
2. **cmd_trading.py**: analyze, buy, scan, status (most visible)
3. **cmd_portfolio.py**: rebalance, backtest, forecast
4. **cmd_intel.py**: news, crypto, volatile
5. **cmd_account.py**: config, alerts, watchlist
6. **Integration**: Test all commands, verify consistency

---

## Success Criteria
- [ ] All panels use bright, vibrant colors (no dim/plain styling for main content)
- [ ] Table headers consistently styled with `bright_cyan`
- [ ] Score values color-coded by quality (green >70, yellow 40-70, red <40)
- [ ] Action verbs (BUY, SELL, ADD, TRIM) use semantic colors
- [ ] All title text bold with appropriate color
- [ ] Panels use `box.ROUNDED` for primary content, `box.SIMPLE` for secondary
- [ ] CLI feels energetic, premium, and engaging
- [ ] No functionality changed, only presentation

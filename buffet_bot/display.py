"""Display helper functions — Rich panels, tables, colored text."""
import json

from rich.panel import Panel
from rich.table import Table
from rich import box

from buffet_bot.globals import console, MODEL_COLORS, _CONFIG


def _make_panel_title(text, color='bright_cyan', icon=None):
    """Create a consistently-styled panel title."""
    icon_str = f"{icon} " if icon else ""
    return f"[bold {color}]{icon_str}{text}[/bold {color}]"


def _bright_color(color: str) -> str:
    """Upgrade dim color names to their bright equivalents for vibrant UI."""
    _MAP = {'cyan': 'bright_cyan', 'magenta': 'bright_magenta'}
    return _MAP.get(color, color)


def _print_ai_responses(responses):
    """Print each model's response in a colored panel."""
    for model, resp in responses.items():
        color = _bright_color(MODEL_COLORS.get(model, 'bright_cyan'))
        content = json.dumps(resp, indent=2) if isinstance(resp, dict) else str(resp)
        console.print(Panel(content, title=_make_panel_title(model, color),
                            border_style=color, box=box.ROUNDED))


def _consensus_text(consensus):
    color = {'BUY': 'bright_green', 'SELL': 'bright_red', 'HOLD': 'bright_yellow'}.get(consensus, 'bright_white')
    return f"[bold {color}]{consensus}[/bold {color}]"


def _score_color(score):
    """Return Rich color for a Buffett score, using config thresholds."""
    disp = _CONFIG.get('display', {})
    green_thresh  = disp.get('buffett_score_green',  70)
    yellow_thresh = disp.get('buffett_score_yellow', 40)
    if score >= green_thresh:
        return 'green'
    if score >= yellow_thresh:
        return 'yellow'
    return 'red'


def _change_color(chg):
    """4-tier color for a price change percentage value."""
    if chg >= 2.0:   return 'bold bright_green'
    if chg >= 0:     return 'green'
    if chg >= -2.0:  return 'red'
    return 'bold bright_red'


def _print_live_market(ticker, realtime, news):
    """Print a live market data panel and recent news table."""
    if realtime:
        sign = '+' if realtime['change_pct'] >= 0 else ''
        pct_color = 'bright_green' if realtime['change_pct'] >= 0 else 'bright_red'
        src = f"[dim] (via {realtime['source']})[/dim]"
        content = (
            f"Price:  [bold bright_white]${realtime['price']:.2f}[/bold bright_white]  "
            f"[{pct_color}]{sign}{realtime['change_pct']}% today[/{pct_color}]{src}\n"
            f"Open: [bright_white]${realtime['open']:.2f}[/bright_white]  "
            f"High: [bright_white]${realtime['high']:.2f}[/bright_white]  "
            f"Low: [bright_white]${realtime['low']:.2f}[/bright_white]  "
            f"Vol: [bright_white]{realtime['volume']:,}[/bright_white]"
        )
        console.print(Panel(content, title=_make_panel_title(f"{ticker} Live Snapshot", 'bright_green'),
                            border_style="bright_green", box=box.ROUNDED))
    else:
        console.print(f"[dim]Live market data unavailable for {ticker}.[/dim]")

    if news:
        table = Table(title="Recent News", box=box.ROUNDED, header_style="bold bright_cyan")
        table.add_column("Date", style="dim bright_white", no_wrap=True)
        table.add_column("Headline", style="bright_white")
        for item in news:
            table.add_row(item['published_at'], item['headline'])
        console.print(table)

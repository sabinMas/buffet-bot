"""Display helper functions — Rich panels, tables, colored text."""
import json

from rich.panel import Panel
from rich.table import Table
from rich import box

from buffet_bot.globals import console, MODEL_COLORS, _CONFIG


def _print_ai_responses(responses):
    """Print each model's response in a colored panel."""
    for model, resp in responses.items():
        color = MODEL_COLORS.get(model, 'white')
        content = json.dumps(resp, indent=2) if isinstance(resp, dict) else str(resp)
        console.print(Panel(content, title=f"[bold {color}]{model}[/bold {color}]",
                            border_style=color))


def _consensus_text(consensus):
    color = {'BUY': 'green', 'SELL': 'red', 'HOLD': 'yellow'}.get(consensus, 'white')
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
        pct_color = 'green' if realtime['change_pct'] >= 0 else 'red'
        src = f"[dim] (via {realtime['source']})[/dim]"
        content = (
            f"Price:  [bold]${realtime['price']:.2f}[/bold]  "
            f"[{pct_color}]{sign}{realtime['change_pct']}% today[/{pct_color}]{src}\n"
            f"Open: ${realtime['open']:.2f}  "
            f"High: ${realtime['high']:.2f}  "
            f"Low: ${realtime['low']:.2f}  "
            f"Vol: {realtime['volume']:,}"
        )
        console.print(Panel(content, title=f"[bold]{ticker}[/bold] Live Market", border_style="green"))
    else:
        console.print(f"[dim]Live market data unavailable for {ticker}.[/dim]")

    if news:
        table = Table(title="Recent News", box=box.SIMPLE, header_style="bold dim")
        table.add_column("Date", style="dim", no_wrap=True)
        table.add_column("Headline")
        for item in news:
            table.add_row(item['published_at'], item['headline'])
        console.print(table)

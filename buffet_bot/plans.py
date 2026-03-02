"""Plan management — save/load/list plans, scheduler, portfolio analysis, guide wizard."""
import os
import json
import pathlib
from datetime import datetime, timezone, timedelta

import click
import yfinance as yf
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich import box

from buffet_bot.globals import (
    PLANS_DIR, GOAL_PRESETS, console,
)
from buffet_bot.display import _score_color, _consensus_text, _print_live_market
from buffet_bot.analysis import _run_analysis, _place_order

# ── Path safety ───────────────────────────────────────────────────────────────

_SCHEDULE_DAYS = {
    'daily':    1,
    'weekly':   7,
    'biweekly': 14,
    'monthly':  30,
}


def _safe_plan_path(name: str) -> pathlib.Path:
    """Return the resolved Path for a plan file, raising ValueError on path traversal."""
    if not name or not name.replace('-', '').replace('_', '').isalnum():
        raise ValueError(f"Invalid plan name: {name!r}")
    plans_dir = pathlib.Path(PLANS_DIR).resolve()
    target = (plans_dir / f"{name}.json").resolve()
    if not str(target).startswith(str(plans_dir) + os.sep):
        raise ValueError(f"Invalid plan name: {name!r}")
    return target


# ── Plan I/O ──────────────────────────────────────────────────────────────────

def _ensure_plans_dir():
    os.makedirs(PLANS_DIR, exist_ok=True)


def _save_plan(name, plan_data):
    _ensure_plans_dir()
    path = _safe_plan_path(name)
    plan_data['name'] = name
    plan_data['updated_at'] = datetime.now().isoformat()
    with open(path, 'w') as f:
        json.dump(plan_data, f, indent=2, default=str)
    return str(path)


def _load_plan(name):
    try:
        path = _safe_plan_path(name)
    except ValueError:
        return None
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _list_plans():
    _ensure_plans_dir()
    result = []
    for fname in sorted(os.listdir(PLANS_DIR)):
        if fname.endswith('.json'):
            try:
                with open(os.path.join(PLANS_DIR, fname)) as f:
                    result.append(json.load(f))
            except Exception:
                pass
    return result


# ── Plan scheduler ────────────────────────────────────────────────────────────

def _is_plan_due(plan: dict) -> bool:
    """Return True if this plan has a schedule and is due to run."""
    schedule = plan.get('schedule')
    if not schedule or schedule not in _SCHEDULE_DAYS:
        return False
    interval = timedelta(days=_SCHEDULE_DAYS[schedule])
    last_run = plan.get('last_run_at')
    if not last_run:
        return True
    try:
        last_dt = datetime.fromisoformat(last_run)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= last_dt + interval
    except Exception:
        return True


def _set_plan_schedule(name: str, schedule: str | None) -> bool:
    """Update a plan's schedule field. Returns True on success."""
    plan = _load_plan(name)
    if plan is None:
        return False
    plan['schedule'] = schedule
    _save_plan(name, plan)
    return True


def _mark_plan_ran(name: str) -> None:
    """Stamp last_run_at = now on a plan after a successful scheduled run."""
    plan = _load_plan(name)
    if plan is not None:
        plan['last_run_at'] = datetime.now(timezone.utc).isoformat()
        _save_plan(name, plan)


# ── Portfolio analysis ────────────────────────────────────────────────────────

def _analyze_portfolio(tickers, budget, risk, primary_model):
    """Analyze a list of tickers and print a summary table. Returns (results, buy_candidates)."""
    results = {}
    for ticker in tickers:
        with console.status(f"[bold blue]Analyzing {ticker}...[/bold blue]"):
            results[ticker] = _run_analysis(ticker, risk, primary_model)

    allocation = budget / len(tickers) if tickers else 0

    table = Table(title="Investment Plan Summary", box=box.ROUNDED, header_style="bold blue")
    table.add_column("Ticker", style="bold cyan")
    table.add_column("Price", justify="right")
    table.add_column("Buffett Score", justify="right")
    table.add_column("Consensus")
    table.add_column("Confidence", justify="right")
    table.add_column("Qty", justify="right")
    table.add_column("Alloc Value", justify="right")

    buy_candidates = []
    for ticker, result in results.items():
        price = result['realtime'].get('price', 0)
        score = result['buffett']['score']
        consensus = result['consensus']
        c_color = {'BUY': 'green', 'SELL': 'red', 'HOLD': 'yellow'}.get(consensus, 'white')
        s_color = _score_color(score)

        qty = 0
        confidence = '—'
        alloc_val = '—'

        if consensus == 'BUY' and result['best_buy_resp']:
            best = result['best_buy_resp']
            confidence = f"{best.get('confidence', 0):.0%}"
            if price > 0:
                qty = max(1, int(allocation / price))
                alloc_val = f"${qty * price:,.2f}"
            else:
                qty = int(best.get('qty', 1))
            buy_candidates.append((ticker, result, qty))

        table.add_row(
            ticker,
            f"${price:.2f}" if price else "—",
            f"[{s_color}]{score}[/{s_color}]",
            f"[{c_color}]{consensus}[/{c_color}]",
            confidence,
            str(qty) if qty else "—",
            alloc_val,
        )

    console.print(table)
    return results, buy_candidates


def _execute_plan_buys(buy_candidates):
    """Prompt the user and place paper orders for each BUY candidate."""
    if not buy_candidates:
        console.print("[yellow]No BUY signals — no orders placed.[/yellow]")
        return

    tickers_str = ", ".join(t for t, _, _ in buy_candidates)
    console.print(f"\n[bold]BUY signals:[/bold] {tickers_str}")

    choice = Prompt.ask(
        "Execute orders? [bold]all[/bold] / [bold]pick[/bold] / [bold]skip[/bold]",
        choices=['all', 'pick', 'skip'],
        default='skip',
    )
    if choice == 'skip':
        return

    for ticker, result, qty in buy_candidates:
        if choice == 'pick' and not click.confirm(f"  Execute BUY {qty}x {ticker}? (Paper)"):
            continue
        best = dict(result['best_buy_resp']) if result['best_buy_resp'] else {}
        best['qty'] = qty
        _place_order(ticker, best)


# ── Guide wizard helpers ──────────────────────────────────────────────────────

def _guide_single_stock(primary_model):
    """Guided single-stock analysis + optional buy."""
    console.print(Panel("[bold]Single Stock Analysis[/bold]", border_style="cyan"))

    raw = Prompt.ask("Ticker symbol or company name").strip()

    ticker = raw.upper()
    if ' ' in raw or len(raw) > 6 or not raw.replace('-', '').replace('.', '').isalpha():
        console.print(f"[dim]Searching for '{raw}'...[/dim]")
        try:
            results = yf.Search(raw).quotes
            if results:
                tbl = Table(box=box.SIMPLE, header_style="bold dim")
                tbl.add_column("#")
                tbl.add_column("Symbol", style="bold cyan")
                tbl.add_column("Name")
                for i, q in enumerate(results[:5], 1):
                    tbl.add_row(str(i), q.get('symbol', ''), q.get('longname') or q.get('shortname', ''))
                console.print(tbl)
                pick = Prompt.ask("Enter number or type a ticker directly", default="1")
                if pick.isdigit() and 1 <= int(pick) <= len(results[:5]):
                    ticker = results[int(pick) - 1].get('symbol', ticker).upper()
                else:
                    ticker = pick.upper().strip()
        except Exception:
            pass

    risk = Prompt.ask("Risk level", choices=['low', 'medium', 'high'], default='medium')

    console.print(f"\n[dim]Running full analysis on {ticker}...[/dim]")
    result = _run_analysis(ticker, risk, primary_model)
    _print_live_market(ticker, result['realtime'], result['news'])
    from buffet_bot.display import _print_ai_responses
    _print_ai_responses(result['responses'])
    console.print(f"\nConsensus: {_consensus_text(result['consensus'])}")

    if result['consensus'] == 'BUY' and result['best_buy_resp']:
        if click.confirm(f"\nExecute BUY {ticker}? (Paper)"):
            _place_order(ticker, result['best_buy_resp'])
    else:
        console.print(f"[yellow]Consensus is {result['consensus']} — no trade recommended.[/yellow]")


def _guide_build_plan(primary_model):
    """Guide the user through building and optionally saving a multi-stock plan."""
    console.print(Panel("[bold]Multi-Stock Investment Plan Builder[/bold]", border_style="cyan"))

    console.print("\n[bold]Choose an investment goal:[/bold]")
    console.print("  [bold cyan]1[/bold cyan]  Growth          (AAPL, MSFT, GOOGL, NVDA, AMZN)")
    console.print("  [bold cyan]2[/bold cyan]  Income/Dividend (JNJ, PG, KO, VZ, ABBV)")
    console.print("  [bold cyan]3[/bold cyan]  Balanced        (V, JPM, BRK-B, SPY, QQQ)")
    console.print("  [bold cyan]4[/bold cyan]  ETF-only        (SPY, QQQ, VTI, SCHD, AGG)")
    console.print("  [bold cyan]5[/bold cyan]  Buffett-style   (BRK-B, KO, AAPL, JNJ, V)")
    console.print("  [bold cyan]6[/bold cyan]  Custom          (enter your own tickers)\n")

    goal_choice = Prompt.ask("Goal", choices=['1', '2', '3', '4', '5', '6'], default='1')
    goal_map = {'1': 'growth', '2': 'income', '3': 'balanced', '4': 'etf', '5': 'buffett', '6': 'custom'}
    goal = goal_map[goal_choice]

    if goal == 'custom':
        raw = Prompt.ask("Enter tickers separated by commas (e.g. AAPL, TSLA, SPY)")
        tickers = [t.strip().upper() for t in raw.split(',') if t.strip()]
    else:
        tickers = list(GOAL_PRESETS[goal])
        console.print(f"[dim]Preset tickers: {', '.join(tickers)}[/dim]")
        if click.confirm("Customize the ticker list?", default=False):
            raw_add = Prompt.ask("Add tickers (comma-separated, or leave blank)", default="")
            raw_rem = Prompt.ask("Remove tickers (comma-separated, or leave blank)", default="")
            adds = [t.strip().upper() for t in raw_add.split(',') if t.strip()]
            removes = [t.strip().upper() for t in raw_rem.split(',') if t.strip()]
            tickers = [t for t in tickers + adds if t not in removes]

    if not tickers:
        console.print("[red]No tickers selected.[/red]")
        return

    budget_str = Prompt.ask("Total investment budget ($)", default="5000")
    try:
        budget = float(budget_str.replace('$', '').replace(',', ''))
    except ValueError:
        console.print("[red]Invalid budget.[/red]")
        return

    risk = Prompt.ask("Risk level", choices=['low', 'medium', 'high'], default='medium')

    console.print(f"\n[dim]Analyzing {len(tickers)} tickers with ${budget:,.2f} budget...[/dim]\n")
    _, buy_candidates = _analyze_portfolio(tickers, budget, risk, primary_model)

    plan_data = {
        'tickers': tickers,
        'budget': budget,
        'risk': risk,
        'goal': goal,
        'created_at': datetime.now().isoformat(),
    }

    if click.confirm("\nSave as a recurring investment plan?", default=False):
        plan_name = Prompt.ask("Plan name", default=f"{goal}-plan")
        try:
            path = _save_plan(plan_name, plan_data)
            console.print(f"[green]Plan saved: {path}[/green]")
            console.print(f"[dim]Re-run any time: buffet-bot guide --plan {plan_name}[/dim]")
        except ValueError as e:
            console.print(f"[red]Could not save plan: {e}[/red]")

    _execute_plan_buys(buy_candidates)


def _guide_load_plan(primary_model):
    """Let the user pick a saved plan and execute it with fresh data."""
    saved = _list_plans()
    if not saved:
        console.print("[yellow]No saved plans found. Build one first with option 2.[/yellow]")
        return

    console.print(Panel("[bold]Saved Investment Plans[/bold]", border_style="cyan"))
    tbl = Table(box=box.SIMPLE, header_style="bold dim")
    tbl.add_column("#")
    tbl.add_column("Name", style="bold cyan")
    tbl.add_column("Goal")
    tbl.add_column("Tickers")
    tbl.add_column("Budget", justify="right")
    tbl.add_column("Saved", style="dim")
    for i, p in enumerate(saved, 1):
        tbl.add_row(
            str(i),
            p.get('name', '?'),
            p.get('goal', 'custom'),
            ', '.join(p.get('tickers', [])),
            f"${p.get('budget', 0):,.2f}",
            (p.get('updated_at') or p.get('created_at', ''))[:10],
        )
    console.print(tbl)

    pick = Prompt.ask("Enter plan number", default="1")
    try:
        plan_data = saved[int(pick) - 1]
    except (ValueError, IndexError):
        console.print("[red]Invalid selection.[/red]")
        return

    _run_guide_plan(plan_data, primary_model)


def _run_guide_plan(plan_data, primary_model):
    """Re-analyze and optionally execute a loaded plan with fresh market data."""
    name = plan_data.get('name', 'unnamed')
    tickers = plan_data.get('tickers', [])
    budget = plan_data.get('budget', 5000)
    risk = plan_data.get('risk', 'medium')

    console.print(Panel(
        f"[bold]Plan: {name}[/bold]\n"
        f"Goal: {plan_data.get('goal', 'custom')}  |  "
        f"Budget: ${budget:,.2f}  |  Risk: {risk}\n"
        f"Tickers: {', '.join(tickers)}",
        border_style="cyan",
    ))

    _, buy_candidates = _analyze_portfolio(tickers, budget, risk, primary_model)
    _execute_plan_buys(buy_candidates)

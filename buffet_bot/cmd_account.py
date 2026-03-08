"""CLI commands: guide, plans, automate, config, alerts, watchlist, beats, completion."""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import click
import ollama as _ollama
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich import box

from buffet_bot.globals import (
    console, MODELS, PLANS_DIR, CONFIG_PATH,
    _CONFIG_DEFAULTS, _load_config, trading_client, ensure_ollama_running,
    LIVE_MODE,
)
from buffet_bot.live_guard import confirm_live_execution
from buffet_bot.display import _make_panel_title
try:
    import tomli_w
except ImportError:
    tomli_w = None

from buffet_bot.data import (
    get_realtime_data, get_buffett_metrics, get_tech_indicators, _complete_ticker,
    _COMMON_TICKERS,
)
from buffet_bot.db import (
    create_alert, get_alerts, delete_alert, mark_alert_triggered,
    add_to_watchlist, remove_from_watchlist, get_watchlist,
    log_earnings_result, get_earnings_history,
)
from buffet_bot.analysis import _run_analysis, _place_order
from buffet_bot.risk import _check_sell_signals
from buffet_bot.plans import (
    _safe_plan_path, _load_plan, _save_plan, _list_plans,
    _is_plan_due, _set_plan_schedule, _mark_plan_ran,
    _run_guide_plan, _guide_single_stock, _guide_build_plan, _guide_load_plan,
)
from buffet_bot.automate import run_agent_loop, SWEEP_AGENT_PROMPT
from buffet_bot.db import create_sweep, complete_sweep, get_sweep_history
from buffet_bot.universe import list_companies, search_companies


# ── guide ─────────────────────────────────────────────────────────────────────

@click.command()
@click.option('--plan', default=None, help='Load and run a saved plan by name.')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def guide(plan, primary_model):
    """Interactive investment wizard: analyze stocks, build plans, and paper trade.

    \b
    Steps through:
      1) Single stock: lookup -> analyze -> optional buy
      2) Multi-stock plan: choose goal -> set budget -> analyze all -> execute
      3) Saved plans: re-analyze with fresh data and optionally re-invest
    """
    # If a plan name is passed directly, skip the menu
    if plan:
        try:
            plan_data = _load_plan(plan)
        except ValueError:
            console.print(f"[red]Invalid plan name: {plan!r}[/red]")
            return
        if not plan_data:
            console.print(f"[red]Plan '{plan}' not found. Run 'buffet-bot plans' to list saved plans.[/red]")
            return
        _run_guide_plan(plan_data, primary_model)
        return

    account = trading_client.get_account()
    console.print(Panel(
        f"[bold bright_green]Buffett Investment Guide[/bold bright_green]\n\n"
        f"Account  Cash [bold bright_white]${float(account.cash):,.2f}[/bold bright_white]  |  "
        f"Buying Power [bold bright_white]${float(account.buying_power):,.2f}[/bold bright_white]\n\n"
        "[dim]Walk through analyzing stocks, build a multi-stock strategy,\n"
        "save recurring investment plans, and execute paper trades.[/dim]",
        border_style="bright_green", box=box.ROUNDED,
    ))

    while True:
        console.print("\n[bold bright_cyan]What would you like to do?[/bold bright_cyan]")
        console.print("  [bold bright_cyan]1[/bold bright_cyan]  Analyze a single stock and optionally buy")
        console.print("  [bold bright_cyan]2[/bold bright_cyan]  Build a multi-stock investment plan")
        console.print("  [bold bright_cyan]3[/bold bright_cyan]  Load and run a saved investment plan")
        console.print("  [bold bright_cyan]q[/bold bright_cyan]  Quit\n")

        try:
            choice = Prompt.ask("[bold]Choice[/bold]", choices=['1', '2', '3', 'q'], default='1')
        except (KeyboardInterrupt, EOFError):
            break

        if choice == 'q':
            break
        elif choice == '1':
            _guide_single_stock(primary_model)
        elif choice == '2':
            _guide_build_plan(primary_model)
        elif choice == '3':
            _guide_load_plan(primary_model)

        if not click.confirm("\nReturn to main menu?", default=True):
            break

    console.print("[dim]Exiting Investment Guide. Good luck investing![/dim]")


# ── plans ─────────────────────────────────────────────────────────────────────

@click.command()
@click.option('--run',          'run_plan',      default=None, help='Execute a saved plan.')
@click.option('--delete',       'delete_plan',   default=None, help='Delete a saved plan.')
@click.option('--schedule',     'set_schedule',  default=None,
              type=click.Tuple([str, click.Choice(['daily', 'weekly', 'biweekly', 'monthly', 'off'])]),
              metavar='NAME FREQ',
              help='Attach a schedule to a plan (e.g. --schedule my-plan weekly).')
@click.option('--run-due',      'run_due',       is_flag=True, default=False,
              help='Run all scheduled plans that are currently due. Cron-friendly.')
@click.option('--model', 'primary_model', default='deepseek-r1', type=click.Choice(MODELS))
def plans(run_plan, delete_plan, set_schedule, run_due, primary_model):
    """List, run, schedule, or delete saved investment plans.

    \b
    Examples:
      buffet-bot plans                              # list all saved plans
      buffet-bot plans --run my-plan               # re-analyze and execute a plan
      buffet-bot plans --schedule my-plan weekly   # run every 7 days
      buffet-bot plans --schedule my-plan off      # remove schedule
      buffet-bot plans --run-due                   # run all plans that are due (put in cron)
      buffet-bot plans --delete my-plan            # remove a plan
    """
    if delete_plan:
        try:
            path = _safe_plan_path(delete_plan)
        except ValueError:
            console.print(f"[bright_red]Invalid plan name: {delete_plan!r}[/bright_red]")
            return
        if path.exists():
            path.unlink()
            console.print(f"[bright_green]Deleted plan '{delete_plan}'.[/bright_green]")
        else:
            console.print(f"[bright_red]Plan '{delete_plan}' not found.[/bright_red]")
        return

    if set_schedule:
        plan_name, freq = set_schedule
        new_sched = None if freq == 'off' else freq
        if _set_plan_schedule(plan_name, new_sched):
            if new_sched:
                console.print(f"[bright_green]Plan '{plan_name}' scheduled: {new_sched}.[/bright_green]")
                console.print(
                    f"[dim]Cron example (daily 09:00): "
                    f"0 9 * * * buffet-bot plans --run-due[/dim]"
                )
            else:
                console.print(f"[yellow]Schedule removed from plan '{plan_name}'.[/yellow]")
        else:
            console.print(f"[red]Plan '{plan_name}' not found.[/red]")
        return

    if run_due:
        all_plans = _list_plans()
        due = [p for p in all_plans if _is_plan_due(p)]
        if not due:
            console.print("[dim]No scheduled plans are due right now.[/dim]")
            return
        console.print(f"[bold cyan]{len(due)} plan(s) due — running now...[/bold cyan]")
        for plan_data in due:
            name = plan_data.get('name', 'unnamed')
            console.print(Panel(
                f"[bold]{name}[/bold]  |  schedule: {plan_data.get('schedule')}",
                title="Running scheduled plan",
                border_style="cyan",
            ))
            _run_guide_plan(plan_data, primary_model)
            _mark_plan_ran(name)
            console.print(f"[green]✓ {name} complete.[/green]")
        return

    if run_plan:
        try:
            plan_data = _load_plan(run_plan)
        except ValueError:
            console.print(f"[red]Invalid plan name: {run_plan!r}[/red]")
            return
        if not plan_data:
            console.print(f"[red]Plan '{run_plan}' not found.[/red]")
            return
        _run_guide_plan(plan_data, primary_model)
        return

    saved = _list_plans()
    if not saved:
        console.print("[yellow]No saved plans. Run 'buffet-bot guide' to create one.[/yellow]")
        return

    table = Table(title="Saved Investment Plans", box=box.ROUNDED, header_style="bold blue")
    table.add_column("Name",     style="bold cyan")
    table.add_column("Goal")
    table.add_column("Tickers")
    table.add_column("Budget",   justify="right")
    table.add_column("Risk")
    table.add_column("Schedule", justify="center")
    table.add_column("Last Run",  style="dim")
    table.add_column("Updated",   style="dim")
    for p in saved:
        sched     = p.get('schedule') or '—'
        last_run  = (p.get('last_run_at') or '')[:10] or '—'
        due_badge = ' [bold yellow]DUE[/bold yellow]' if _is_plan_due(p) else ''
        table.add_row(
            p.get('name', '?'),
            p.get('goal', 'custom'),
            ', '.join(p.get('tickers', [])),
            f"${p.get('budget', 0):,.2f}",
            p.get('risk', 'medium'),
            f"{sched}{due_badge}",
            last_run,
            (p.get('updated_at') or p.get('created_at', ''))[:10],
        )
    console.print(table)
    console.print("\n[dim]Run:       buffet-bot plans --run <name>[/dim]")
    console.print("[dim]Schedule:  buffet-bot plans --schedule <name> daily|weekly|biweekly|monthly[/dim]")
    console.print("[dim]Run due:   buffet-bot plans --run-due  (put this in cron)[/dim]")
    console.print("[dim]Delete:    buffet-bot plans --delete <name>[/dim]")


# ── automate ──────────────────────────────────────────────────────────────────

def _build_automate_tools(execute: bool, budget: float, primary_model: str, risk: str = 'medium', strategy: str = 'value', speculative: bool = False) -> dict:
    """Return the tool registry for the automate agent loop."""
    spent = [0.0]  # mutable closure for budget tracking

    DEFAULT_SCAN_TICKERS = [
        'AAPL', 'MSFT', 'GOOGL', 'BRK-B', 'JNJ', 'V', 'JPM', 'PG',
        'KO', 'WMT', 'ABBV', 'MRK', 'LLY', 'TMO', 'UNH', 'HD', 'AMZN', 'NVDA', 'META', 'TSLA',
    ]

    def scan_stocks(top=5):
        tickers = DEFAULT_SCAN_TICKERS
        results = {}
        with ThreadPoolExecutor(max_workers=min(len(tickers), 2)) as pool:
            futures = {pool.submit(get_buffett_metrics, t): t for t in tickers}
            for fut in as_completed(futures):
                t = futures[fut]
                try:
                    results[t] = fut.result()
                except Exception:
                    results[t] = {'score': 0}
        ranked = sorted(results.items(), key=lambda x: x[1].get('score', 0), reverse=True)
        return [{'ticker': t, 'score': m.get('score', 0)} for t, m in ranked[:int(top)]]

    def analyze_stock(ticker, risk=risk, strategy=strategy):
        ticker = ticker.upper()
        try:
            data = _run_analysis(ticker, risk, primary_model, strategy)
        except Exception as e:
            return {"error": f"Analysis failed for {ticker}: {e}", "consensus": "HOLD"}
        realtime = data.get('realtime') or {}
        buffett  = data.get('buffett')  or {}
        best     = data.get('best_buy_resp') or {}
        return {
            'ticker':        ticker,
            'consensus':     data.get('consensus', 'HOLD'),
            'buffett_score': buffett.get('score', 0),
            'price':         realtime.get('price', 0),
            'reason':        best.get('reason', ''),
            'suggested_qty': best.get('qty', 0),
        }

    def get_portfolio():
        try:
            positions = trading_client.get_all_positions()
            return [
                {
                    'ticker':        p.symbol,
                    'qty':           p.qty,
                    'market_value':  float(p.market_value),
                    'unrealized_pl': float(p.unrealized_pl),
                }
                for p in positions
            ]
        except Exception as e:
            return {'error': str(e)}

    def get_account_status():
        try:
            acct = trading_client.get_account()
            return {
                'cash':          float(acct.cash),
                'buying_power':  float(acct.buying_power),
            }
        except Exception as e:
            return {'error': str(e)}

    def check_sell_candidates():
        try:
            positions = trading_client.get_all_positions()
            if not positions:
                return []
            tuples = _check_sell_signals(positions)
            return [
                {'ticker': pos.symbol, 'signals': sigs}
                for pos, sigs, _score, _rsi in tuples
                if sigs
            ]
        except Exception as e:
            return {'error': str(e)}

    def search_companies_tool(query):
        try:
            return list(search_companies(query, limit=10))
        except Exception as e:
            return {'error': str(e)}

    def browse_sector(sector):
        try:
            return list(list_companies(sector=sector, limit=20))
        except Exception as e:
            return {'error': str(e)}

    def buy_stock(ticker, qty):
        if not execute:
            return {'executed': False, 'reason': 'dry-run mode — pass --execute to place orders'}
        ticker = ticker.upper()
        qty    = int(qty)
        price  = (get_realtime_data(ticker) or {}).get('price', 0) or 0
        cost   = price * qty
        if spent[0] + cost > budget:
            return {
                'executed': False,
                'reason':   f'exceeds budget (${spent[0]:.2f} spent of ${budget:.2f})',
            }
        if not confirm_live_execution(
            f"BUY {qty}x {ticker}", ticker, qty, "BUY",
            estimated_cost=cost,
        ):
            return {'executed': False, 'reason': 'live execution not confirmed'}
        _place_order(ticker, {'qty': qty})
        spent[0] += cost
        return {
            'executed':       True,
            'ticker':         ticker,
            'qty':            qty,
            'estimated_cost': round(cost, 2),
            'total_spent':    round(spent[0], 2),
        }

    def scan_speculative_stocks(top=10):
        from buffet_bot.volatile import scan_volatile, VOLATILE_UNIVERSE
        results = scan_volatile(universe=VOLATILE_UNIVERSE, n=int(top))
        return [
            {
                'ticker':    r['ticker'],
                'vol_score': r['score'],
                'beta':      r.get('beta', 0),
                'short_pct': r.get('short_pct', 0),
                'vol_30d':   r.get('vol_30d', 0),
            }
            for r in results
        ]

    base_tools = {
        'scan_stocks': {
            'description': 'Scan default watchlist for Buffett-scored opportunities',
            'params':      'top=5',
            'fn':          scan_stocks,
        },
        'analyze_stock': {
            'description': 'Run full LLM analysis on a single ticker',
            'params':      'ticker, risk="medium", strategy="value"',
            'fn':          analyze_stock,
        },
        'get_portfolio': {
            'description': 'List current Alpaca paper portfolio positions',
            'params':      '',
            'fn':          get_portfolio,
        },
        'get_account_status': {
            'description': 'Get paper account cash and buying power',
            'params':      '',
            'fn':          get_account_status,
        },
        'check_sell_candidates': {
            'description': 'Check all positions for sell signals (stop/underperform/overbought)',
            'params':      '',
            'fn':          check_sell_candidates,
        },
        'search_companies': {
            'description': 'Search the company database by keyword or name',
            'params':      'query',
            'fn':          search_companies_tool,
        },
        'browse_sector': {
            'description': 'List companies in a sector (e.g. "Technology", "Healthcare")',
            'params':      'sector',
            'fn':          browse_sector,
        },
        'buy_stock': {
            'description': 'Place a paper market BUY order (respects --execute and --budget)',
            'params':      'ticker, qty',
            'fn':          buy_stock,
        },
    }
    if speculative:
        base_tools['scan_speculative_stocks'] = {
            'description': 'Scan the volatile/speculative universe (meme stocks, small-cap, high-beta) ranked by volatility score',
            'params':      'top=10',
            'fn':          scan_speculative_stocks,
        }
    return base_tools


@click.command()
@click.argument('goal', required=False, default=None)
@click.option('--execute', 'execute', is_flag=True, default=False,
              help='Allow paper trades. Default is dry-run.')
@click.option('--budget', default=500.0, show_default=True, type=float,
              help='Max capital to spend this session (paper $).')
@click.option('--max-steps', default=10, show_default=True, type=int,
              help='Max agent steps before stopping.')
@click.option('--model', 'primary_model', default=MODELS[0], show_default=True,
              type=click.Choice(MODELS),
              help='LLM to use as the agent brain.')
@click.option('--risk', default=None, type=click.Choice(['low', 'medium', 'high']),
              help='Risk appetite (low/medium/high). Prompted if omitted in wizard mode.')
@click.option('--strategy', default=None,
              type=click.Choice(['value', 'growth', 'dividend', 'turnaround', 'speculative']),
              help='Investing strategy. Prompted if omitted in wizard mode.')
@click.option('--speculative', 'speculative', is_flag=True, default=False,
              help='Enable speculative/volatile stock scanning (penny stocks, high-beta, short-squeeze candidates).')
@click.option('--sweep', 'sweep', is_flag=True, default=False,
              help='Deterministic sweep mode: scan > rank > size > execute top-N. Logged to sweeps table.')
def automate(goal, execute, budget, max_steps, primary_model, risk, strategy, speculative, sweep):
    """AI agent that autonomously chains buffet-bot tools to accomplish a goal.

    \b
    Examples:
      buffet-bot automate
      buffet-bot automate "find the top 3 value stocks"
      buffet-bot automate "invest $500 in the best stock" --execute --budget 500 --risk high
      buffet-bot automate "should I sell anything?" --max-steps 5
    """
    if not ensure_ollama_running():
        return
    if speculative and not goal:
        # Fast path: --speculative with no goal skips the wizard
        if not risk:
            risk = 'high'
        if not strategy:
            strategy = 'speculative'
        goal = (
            f"Scan the volatile/speculative universe for top momentum plays. "
            f"Use a ${budget:.2f} budget with tight stops."
        )
    elif not goal:
        console.print(Panel(
            "Let's set up your automated investing session.",
            title="[bold cyan]Buffet-Bot Automate[/bold cyan]",
            border_style="cyan",
        ))
        budget_str = Prompt.ask("How much do you want to invest?", default=f"{budget:.2f}")
        try:
            budget = float(budget_str.replace('$', '').replace(',', ''))
        except ValueError:
            console.print("[red]Invalid amount.[/red]")
            return
        if not risk:
            risk = Prompt.ask("Risk level", choices=['low', 'medium', 'high'], default='medium')
        if not strategy:
            strategy = Prompt.ask("Strategy", choices=['value', 'growth', 'dividend', 'turnaround'], default='value')
        if not execute:
            execute = click.confirm("Execute real paper trades?", default=False)
        goal = (
            f"Invest ${budget:.2f} using a {risk}-risk {strategy} strategy. "
            f"Scan for top candidates, analyze them, and buy the best opportunities."
        )
    else:
        if not risk:
            risk = 'medium'
        if speculative and not strategy:
            strategy = 'speculative'
        elif not strategy:
            strategy = 'value'

    tools = _build_automate_tools(execute=execute, budget=budget, primary_model=primary_model,
                                   risk=risk, strategy=strategy, speculative=speculative)

    sweep_id = 0
    if sweep:
        if not goal:
            goal = (
                f"Sweep the top value stocks. Invest up to ${budget:.2f} using a "
                f"{risk}-risk {strategy} strategy by scanning, analyzing, and buying the best opportunities."
            )
        sweep_id = create_sweep(goal=goal, budget_usd=budget)

    mode_label = "[bold bright_red]SWEEP[/bold bright_red]" if sweep else \
                 ('[green]EXECUTE[/green]' if execute else '[yellow]DRY RUN[/yellow]')
    console.print(Panel(
        f"[bold]Goal:[/bold] {goal}\n"
        f"[bold]Budget:[/bold] ${budget:,.2f}  "
        f"[bold]Risk:[/bold] {risk}  "
        f"[bold]Strategy:[/bold] {strategy}  "
        f"[bold]Mode:[/bold] {mode_label}  "
        f"[bold]Max steps:[/bold] {max_steps}  "
        f"[bold]Model:[/bold] {primary_model}",
        title="[bold cyan]Buffet-Bot Automate[/bold cyan]",
        border_style="cyan",
    ))

    result = run_agent_loop(
        goal=goal,
        tools=tools,
        model=primary_model,
        budget=budget,
        max_steps=max_steps,
        execute=execute,
        console=console,
        ollama_module=_ollama,
        risk=risk,
        strategy=strategy,
        system_prompt_template=SWEEP_AGENT_PROMPT if sweep else None,
    )

    if sweep and sweep_id:
        # Count orders placed by inspecting tool results would require deep parsing;
        # use summary string as best-effort and mark complete.
        complete_sweep(
            sweep_id=sweep_id,
            tickers_scanned=result.get('steps_taken', 0),
            orders_placed=0,  # ENG-TODO: instrument buy_stock tool to count orders
            total_deployed=0.0,
            summary=result.get('summary', ''),
            status='FAILED' if result.get('timed_out') else 'COMPLETE',
        )
        console.print(f"[dim]Sweep #{sweep_id} logged.[/dim]")

    if result.get('timed_out'):
        console.print(
            f"[yellow]Agent stopped after {max_steps} steps without calling done().[/yellow]"
        )


# ── config group ──────────────────────────────────────────────────────────────

@click.group()
def config():
    """View or edit ~/.buffet-bot-config.toml."""
    pass


@config.command('show')
def config_show():
    """Show effective config (file values merged with defaults): buffet-bot config show"""
    file_exists = os.path.exists(CONFIG_PATH)
    source = f"[dim](from {CONFIG_PATH})[/dim]" if file_exists else "[dim](defaults only — no config file)[/dim]"
    cfg = _load_config()

    table = Table(title=f"Effective Config  {source}", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Section", style="bold", no_wrap=True)
    table.add_column("Key", style="bold")
    table.add_column("Value")
    for section, values in cfg.items():
        for key, val in values.items():
            table.add_row(section, key, str(val))
    console.print(table)
    if not file_exists:
        console.print(f"[dim]Create a config file with: buffet-bot config init[/dim]")


@config.command('init')
@click.option('--force', is_flag=True, default=False, help='Overwrite existing config file.')
def config_init(force):
    """Write a default config file at ~/.buffet-bot-config.toml: buffet-bot config init"""
    if tomli_w is None:
        console.print("[red]tomli-w not installed. Run: pip install tomli-w[/red]")
        return
    if os.path.exists(CONFIG_PATH) and not force:
        console.print(f"[yellow]Config file already exists: {CONFIG_PATH}[/yellow]")
        console.print("[dim]Use --force to overwrite.[/dim]")
        return
    with open(CONFIG_PATH, 'wb') as f:
        tomli_w.dump(_CONFIG_DEFAULTS, f)
    console.print(f"[green]Config written: {CONFIG_PATH}[/green]")
    console.print("[dim]Edit it with any text editor, then re-run your commands.[/dim]")


# ── alerts group ──────────────────────────────────────────────────────────────

@click.group()
def alerts():
    """Set and check price/RSI threshold alerts."""
    pass


@alerts.command('set')
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--price-above', type=float, default=None, help='Trigger when price rises above this value.')
@click.option('--price-below', type=float, default=None, help='Trigger when price falls below this value.')
@click.option('--rsi-above',   type=float, default=None, help='Trigger when RSI rises above this value.')
@click.option('--rsi-below',   type=float, default=None, help='Trigger when RSI falls below this value.')
@click.option('--note', default='', help='Optional label for this alert.')
def alerts_set(ticker, price_above, price_below, rsi_above, rsi_below, note):
    """Set a price or RSI alert: buffet-bot alerts set AAPL --price-above 200"""
    ticker = ticker.upper()
    conditions = {
        'price_above': price_above,
        'price_below': price_below,
        'rsi_above':   rsi_above,
        'rsi_below':   rsi_below,
    }
    created = [(t, v) for t, v in conditions.items() if v is not None]
    if not created:
        console.print("[red]Specify at least one condition: --price-above, --price-below, --rsi-above, --rsi-below[/red]")
        return
    for alert_type, threshold in created:
        row_id = create_alert(ticker, alert_type, threshold, note)
        label = alert_type.replace('_', ' ')
        console.print(f"[green]Alert #{row_id} set:[/green] {ticker} {label} {threshold}"
                      + (f"  [dim]{note}[/dim]" if note else ""))


@alerts.command('list')
def alerts_list():
    """Show all active alerts: buffet-bot alerts list"""
    items = get_alerts(triggered=False)
    if not items:
        console.print("[yellow]No active alerts. Set one with: buffet-bot alerts set AAPL --price-above 200[/yellow]")
        return
    table = Table(title="Active Alerts", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("ID",        justify="right", style="dim")
    table.add_column("Ticker",    style="bold", no_wrap=True)
    table.add_column("Condition")
    table.add_column("Threshold", justify="right")
    table.add_column("Note",      style="dim")
    table.add_column("Set",       style="dim")
    for a in items:
        table.add_row(
            str(a['id']),
            a['ticker'],
            a['type'].replace('_', ' '),
            str(a['threshold']),
            a['note'],
            a['created_at'][:10],
        )
    console.print(table)


@alerts.command('remove')
@click.argument('alert_id', type=int)
def alerts_remove(alert_id):
    """Remove an alert by ID: buffet-bot alerts remove 3"""
    delete_alert(alert_id)
    console.print(f"[yellow]Alert #{alert_id} removed.[/yellow]")


@alerts.command('check')
def alerts_check():
    """Check all active alerts against current market data: buffet-bot alerts check"""
    items = get_alerts(triggered=False)
    if not items:
        console.print("[yellow]No active alerts to check.[/yellow]")
        return

    # Gather unique tickers and which data types we need per ticker
    tickers_needing_rsi = {a['ticker'] for a in items if 'rsi' in a['type']}
    unique_tickers = {a['ticker'] for a in items}

    prices = {}
    rsi_values = {}
    with console.status("[bold blue]Fetching market data...[/bold blue]"):
        for ticker in unique_tickers:
            live = get_realtime_data(ticker)
            prices[ticker] = live.get('price')
            if ticker in tickers_needing_rsi:
                tech = get_tech_indicators(ticker)
                rsi_values[ticker] = tech.get('rsi')

    triggered_ids = []
    table = Table(title="Alert Check", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("ID",        justify="right", style="dim")
    table.add_column("Ticker",    style="bold", no_wrap=True)
    table.add_column("Condition")
    table.add_column("Threshold", justify="right")
    table.add_column("Current",   justify="right")
    table.add_column("Status")

    for a in items:
        ticker    = a['ticker']
        threshold = a['threshold']
        atype     = a['type']

        if 'price' in atype:
            current = prices.get(ticker)
            label   = f"${current:.2f}" if current is not None else "—"
        else:
            current = rsi_values.get(ticker)
            label   = f"{current:.1f}" if current is not None else "—"

        fired = False
        if current is not None:
            if atype == 'price_above' and current > threshold:
                fired = True
            elif atype == 'price_below' and current < threshold:
                fired = True
            elif atype == 'rsi_above' and current > threshold:
                fired = True
            elif atype == 'rsi_below' and current < threshold:
                fired = True

        if fired:
            triggered_ids.append(a['id'])
            status_cell = "[bold green]TRIGGERED[/bold green]"
        else:
            status_cell = "[dim]waiting[/dim]"

        table.add_row(
            str(a['id']),
            ticker,
            atype.replace('_', ' '),
            str(threshold),
            label,
            status_cell,
        )

    console.print(table)

    if triggered_ids:
        console.print(f"\n[bold green]{len(triggered_ids)} alert(s) triggered.[/bold green]")
        for aid in triggered_ids:
            mark_alert_triggered(aid)
        console.print("[dim]Triggered alerts removed from active list.[/dim]")
    else:
        console.print("[dim]No alerts triggered.[/dim]")


# ── watchlist group ───────────────────────────────────────────────────────────

@click.group()
def watchlist():
    """Manage your personal stock watchlist."""
    pass


@watchlist.command('add')
@click.argument('ticker', shell_complete=_complete_ticker)
def watchlist_add(ticker):
    """Add a ticker to your watchlist: buffet-bot watchlist add TSLA"""
    ticker = ticker.upper()
    add_to_watchlist(ticker)
    console.print(f"[green]Added [bold]{ticker}[/bold] to watchlist.[/green]")


@watchlist.command('remove')
@click.argument('ticker', shell_complete=_complete_ticker)
def watchlist_remove(ticker):
    """Remove a ticker from your watchlist: buffet-bot watchlist remove TSLA"""
    ticker = ticker.upper()
    remove_from_watchlist(ticker)
    console.print(f"[yellow]Removed [bold]{ticker}[/bold] from watchlist.[/yellow]")


@watchlist.command('show')
def watchlist_show():
    """Show all tickers in your watchlist: buffet-bot watchlist show"""
    items = get_watchlist()
    if not items:
        console.print("[yellow]Your watchlist is empty. Add tickers with: buffet-bot watchlist add TSLA[/yellow]")
        return
    table = Table(title="My Watchlist", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Ticker", style="bold", no_wrap=True)
    table.add_column("Added", style="dim")
    for item in items:
        table.add_row(item['ticker'], item['added_at'])
    console.print(table)
    console.print(f"[dim]{len(items)} ticker(s). Scan with: buffet-bot scan --watchlist[/dim]")


# ── beats group ───────────────────────────────────────────────────────────────

@click.group()
def beats():
    """Log and review earnings beat/miss history."""
    pass


@beats.command('log')
@click.argument('ticker', shell_complete=_complete_ticker)
@click.option('--eps-actual',   required=True, type=float, help='Reported EPS.')
@click.option('--eps-forecast', required=True, type=float, help='Analyst consensus EPS forecast.')
@click.option('--date', 'report_date', default=None,
              help='Report date YYYY-MM-DD (defaults to today).')
def beats_log(ticker, eps_actual, eps_forecast, report_date):
    """Record an earnings result against the analyst forecast.

    \b
    Examples:
      buffet-bot beats log AAPL --eps-actual 2.18 --eps-forecast 2.10
      buffet-bot beats log MSFT --eps-actual 3.05 --eps-forecast 3.20 --date 2026-01-29
    """
    ticker = ticker.upper()
    if report_date is None:
        report_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    inserted = log_earnings_result(ticker, report_date, eps_actual, eps_forecast)
    if not inserted:
        console.print(f"[yellow]Duplicate entry: {ticker} on {report_date} already logged.[/yellow]")
        return
    surprise_pct = ((eps_actual - eps_forecast) / abs(eps_forecast) * 100
                    if eps_forecast != 0 else 0.0)
    sign   = '+' if surprise_pct >= 0 else ''
    color  = 'green' if surprise_pct >= 3 else ('red' if surprise_pct <= -3 else 'yellow')
    label  = 'BEAT' if surprise_pct >= 3 else ('MISS' if surprise_pct <= -3 else 'IN-LINE')
    console.print(Panel(
        f"Ticker:    [bold cyan]{ticker}[/bold cyan]\n"
        f"Date:      {report_date}\n"
        f"Actual:    ${eps_actual:.4f}\n"
        f"Forecast:  ${eps_forecast:.4f}\n"
        f"Surprise:  [{color}][bold]{sign}{surprise_pct:.1f}%  {label}[/bold][/{color}]",
        title="[bold]Earnings Result Logged[/bold]",
        border_style=color,
    ))


@beats.command('show')
@click.argument('ticker', default='', required=False, shell_complete=_complete_ticker)
@click.option('--limit', default=20, show_default=True, type=int,
              help='Max rows to show.')
def beats_show(ticker, limit):
    """Display earnings beat/miss history.

    \b
    Examples:
      buffet-bot beats show          # all tickers, most recent first
      buffet-bot beats show AAPL     # just Apple
      buffet-bot beats show AAPL --limit 8
    """
    rows = get_earnings_history(ticker, limit)
    if not rows:
        msg = f"No earnings history for [bold]{ticker.upper()}[/bold]." if ticker \
              else "No earnings history logged yet. Use: buffet-bot beats log TICKER --eps-actual X --eps-forecast Y"
        console.print(f"[yellow]{msg}[/yellow]")
        return

    tbl = Table(
        title=f"Earnings Surprise History — {ticker.upper() or 'All Tickers'}",
        box=box.ROUNDED, header_style="bold blue",
    )
    tbl.add_column("Ticker",   style="bold cyan",  no_wrap=True)
    tbl.add_column("Date",     style="dim",         no_wrap=True)
    tbl.add_column("Actual",   justify="right")
    tbl.add_column("Forecast", justify="right")
    tbl.add_column("Surprise", justify="right")
    tbl.add_column("Result",   justify="center")

    beat_count = miss_count = inline_count = 0
    for r in rows:
        bm = r['beat_miss']
        color = 'green' if bm == 'BEAT' else ('red' if bm == 'MISS' else 'yellow')
        sign  = '+' if r['surprise_pct'] >= 0 else ''
        tbl.add_row(
            r['ticker'],
            r['report_date'],
            f"${r['eps_actual']:.4f}",
            f"${r['eps_forecast']:.4f}",
            f"[{color}]{sign}{r['surprise_pct']:.1f}%[/{color}]",
            f"[{color}][bold]{bm}[/bold][/{color}]",
        )
        if bm == 'BEAT':    beat_count  += 1
        elif bm == 'MISS':  miss_count  += 1
        else:               inline_count += 1

    console.print(tbl)
    total = beat_count + miss_count + inline_count
    beat_rate = beat_count / total * 100 if total else 0
    console.print(
        f"  [green]Beats: {beat_count}[/green]  "
        f"[red]Misses: {miss_count}[/red]  "
        f"[yellow]In-line: {inline_count}[/yellow]  "
        f"[dim]Beat rate: {beat_rate:.0f}%[/dim]"
    )


# ── completion ────────────────────────────────────────────────────────────────

@click.command()
@click.argument('shell', type=click.Choice(['bash', 'zsh', 'fish']), default='bash')
def completion(shell):
    """Print shell tab-completion setup: buffet-bot completion bash|zsh|fish"""
    prog = 'buffet-bot'
    env_var = '_BUFFET_BOT_COMPLETE'
    if shell == 'bash':
        line = f'eval "$({env_var}=bash_source {prog})"'
        dest = '~/.bashrc'
    elif shell == 'zsh':
        line = f'eval "$({env_var}=zsh_source {prog})"'
        dest = '~/.zshrc'
    else:
        line = f'{env_var}=fish_source {prog} | source'
        dest = '~/.config/fish/completions/buffet-bot.fish'
    console.print(Panel(
        f"Add this line to [cyan]{dest}[/cyan] then restart your shell:\n\n"
        f"  [bold green]{line}[/bold green]\n\n"
        f"[dim]Completes commands, --risk/--model/--strategy options, and TICKER "
        f"arguments (your watchlist + {len(_COMMON_TICKERS)} common stocks).[/dim]",
        title="[bold]Shell Completion Setup[/bold]",
        border_style="cyan",
    ))


# ── automate-crypto ────────────────────────────────────────────────────────────

def _build_crypto_tools(execute: bool, budget: float, primary_model: str) -> dict:
    """Return the tool registry for the automate-crypto agent loop."""
    spent = [0.0]

    def scan_cryptos(top=5):
        from buffet_bot.crypto import CRYPTO_SYMBOLS, get_crypto_volatility
        results = []
        for sym in CRYPTO_SYMBOLS:
            v = get_crypto_volatility(sym)
            if v:
                results.append({'symbol': sym, **v})
        results.sort(key=lambda x: abs(x.get('return_30d', 0)), reverse=True)
        return results[:int(top)]

    def analyze_crypto_asset(symbol):
        from buffet_bot.crypto import get_crypto_bars, get_crypto_quote, get_crypto_volatility
        import ollama as _ollama_inner
        bars  = get_crypto_bars(symbol, days=14)
        quote = get_crypto_quote(symbol)
        vol   = get_crypto_volatility(symbol) or {}
        price = quote.get('mid', vol.get('last_price', 0))
        prompt = (
            f"Crypto: {symbol}\n"
            f"Current price: ${price:.4f}\n"
            f"30d return: {vol.get('return_30d', 0):.1f}%\n"
            f"Annualized vol: {vol.get('vol_30d_annualized', 0):.1f}%\n"
            f"Max drawdown: {vol.get('max_drawdown', 0):.1f}%\n\n"
            "Reply ONLY with JSON: "
            "{\"action\":\"BUY|SELL|HOLD\",\"confidence\":0.0-1.0,"
            "\"usd_amount\":50,\"reason\":\"one sentence\"}"
        )
        try:
            import re as _re, json as _json
            resp = _ollama_inner.chat(
                model=primary_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.1},
            )
            text = _re.sub(r'<think>.*?</think>', '', resp["message"]["content"], flags=_re.DOTALL)
            data = _json.loads(text[text.find('{'):text.rfind('}')+1])
            return {
                'symbol':     symbol,
                'consensus':  data.get('action', 'HOLD'),
                'confidence': data.get('confidence', 0),
                'usd_amount': data.get('usd_amount', 50),
                'reason':     data.get('reason', ''),
                'price':      price,
            }
        except Exception as e:
            return {'symbol': symbol, 'consensus': 'HOLD', 'reason': str(e)}

    def get_crypto_portfolio():
        from buffet_bot.crypto import get_coinbase_balance
        return get_coinbase_balance() or {'total_usd': 0, 'accounts': []}

    def buy_crypto(symbol, usd_amount):
        if not execute:
            return {'executed': False, 'reason': 'dry-run — pass --execute'}
        usd_amount = float(usd_amount)
        if spent[0] + usd_amount > budget:
            return {'executed': False, 'reason': f'exceeds budget (${spent[0]:.2f} of ${budget:.2f} spent)'}
        from buffet_bot.crypto import coinbase_market_buy
        try:
            result = coinbase_market_buy(symbol, usd_amount)
            spent[0] += usd_amount
            return {'executed': True, 'symbol': symbol, 'usd_amount': usd_amount,
                    'total_spent': round(spent[0], 2), 'order': str(result)[:120]}
        except Exception as e:
            return {'executed': False, 'error': str(e)}

    return {
        'scan_cryptos': {
            'description': 'Scan all crypto assets ranked by absolute 30-day price movement',
            'params':      'top=5',
            'fn':          scan_cryptos,
        },
        'analyze_crypto_asset': {
            'description': 'LLM analysis of a crypto asset — returns BUY/SELL/HOLD with confidence',
            'params':      'symbol',
            'fn':          analyze_crypto_asset,
        },
        'get_crypto_portfolio': {
            'description': 'Get current Coinbase balances',
            'params':      '',
            'fn':          get_crypto_portfolio,
        },
        'buy_crypto': {
            'description': 'Market buy a crypto asset with a USD amount via Coinbase',
            'params':      'symbol, usd_amount',
            'fn':          buy_crypto,
        },
    }


@click.command('automate-crypto')
@click.argument('goal', required=False, default=None)
@click.option('--execute', is_flag=True, default=False,
              help='Allow live crypto trades. Default is dry-run.')
@click.option('--budget', default=200.0, show_default=True, type=float,
              help='Max USD to spend this session.')
@click.option('--max-steps', default=8, show_default=True, type=int)
@click.option('--model', 'primary_model', default=MODELS[0],
              type=click.Choice(MODELS))
def automate_crypto(goal, execute, budget, max_steps, primary_model):
    """AI agent that autonomously scans and trades crypto assets.

    \b
    Examples:
      buffet-bot automate-crypto
      buffet-bot automate-crypto "find the best crypto to buy right now" --execute --budget 200
    """
    if not ensure_ollama_running():
        return
    if not goal:
        goal = f"Scan all crypto assets for the best momentum play and invest up to ${budget:.2f}."

    from buffet_bot.automate import CRYPTO_AGENT_PROMPT
    tools = _build_crypto_tools(execute=execute, budget=budget, primary_model=primary_model)

    console.print(Panel(
        f"[bold]Goal:[/bold] {goal}\n"
        f"[bold]Budget:[/bold] ${budget:,.2f}  "
        f"[bold]Mode:[/bold] {'[green]EXECUTE[/green]' if execute else '[yellow]DRY RUN[/yellow]'}  "
        f"[bold]Max steps:[/bold] {max_steps}  "
        f"[bold]Model:[/bold] {primary_model}",
        title="[bold cyan]Buffet-Bot Automate Crypto[/bold cyan]",
        border_style="cyan",
    ))

    run_agent_loop(
        goal, tools,
        model=primary_model, budget=budget, max_steps=max_steps,
        execute=execute, console=console, ollama_module=_ollama,
        risk='high', strategy='crypto',
        system_prompt_template=CRYPTO_AGENT_PROMPT,
    )


# ── automate-options ───────────────────────────────────────────────────────────

def _build_options_tools(execute: bool, budget: float, primary_model: str, ticker_list: list) -> dict:
    """Return the tool registry for the automate-options agent loop."""
    spent = [0.0]

    def analyze_stock_direction(ticker):
        ticker = ticker.upper()
        from buffet_bot.analysis import _run_analysis
        data     = _run_analysis(ticker, 'high', primary_model, 'growth')
        realtime = data.get('realtime') or {}
        best     = data.get('best_buy_resp') or {}
        return {
            'ticker':     ticker,
            'direction':  data.get('consensus', 'HOLD'),
            'confidence': best.get('confidence', 0),
            'reason':     best.get('reason', ''),
            'spot_price': realtime.get('price', 0),
        }

    def get_options_chain(ticker, side='call', expiry=None):
        import yfinance as _yf
        import datetime
        ticker = ticker.upper()
        t = _yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return {'error': f'No options data for {ticker}'}
        selected = expiry or expirations[0]
        if not expiry:
            target = datetime.date.today() + datetime.timedelta(days=35)
            for exp in expirations:
                if datetime.date.fromisoformat(exp) >= target:
                    selected = exp
                    break
        chain = t.option_chain(selected)
        df = chain.calls if side == 'call' else chain.puts
        from buffet_bot.data import get_realtime_data
        spot = (get_realtime_data(ticker) or {}).get('price', 0)
        if spot:
            atm_idx = (df['strike'] - spot).abs().nsmallest(5).index
            df = df.loc[atm_idx]
        else:
            df = df.nlargest(5, 'volume')
        rows = []
        for _, row in df.iterrows():
            rows.append({
                'contractSymbol': row['contractSymbol'],
                'strike':         row['strike'],
                'bid':            round(float(row.get('bid', 0) or 0), 2),
                'ask':            round(float(row.get('ask', 0) or 0), 2),
                'volume':         int(row.get('volume', 0) or 0),
                'iv':             round(float(row.get('impliedVolatility', 0) or 0), 3),
                'expiry':         selected,
                'inTheMoney':     bool(row.get('inTheMoney', False)),
            })
        return {'side': side, 'ticker': ticker, 'expiry': selected, 'contracts': rows}

    def buy_option_contract(contract_symbol, qty=1):
        if not execute:
            return {'executed': False, 'reason': 'dry-run — pass --execute'}
        qty = int(qty)
        if spent[0] > budget:
            return {'executed': False, 'reason': 'exceeds budget'}
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            order = trading_client.submit_order(MarketOrderRequest(
                symbol=contract_symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            ))
            return {
                'executed':        True,
                'contract_symbol': contract_symbol,
                'qty':             qty,
                'order_id':        str(order.id),
            }
        except Exception as e:
            return {'executed': False, 'error': str(e)}

    def list_tickers():
        return ticker_list

    return {
        'list_tickers': {
            'description': 'List the tickers available for options analysis',
            'params':      '',
            'fn':          list_tickers,
        },
        'analyze_stock_direction': {
            'description': 'LLM analysis of stock direction — returns BUY/SELL/HOLD + spot price',
            'params':      'ticker',
            'fn':          analyze_stock_direction,
        },
        'get_options_chain': {
            'description': 'Fetch ATM/near-OTM options contracts for a ticker (call or put)',
            'params':      'ticker, side="call", expiry=None',
            'fn':          get_options_chain,
        },
        'buy_option_contract': {
            'description': 'Place a paper options BUY order via Alpaca (requires options enabled)',
            'params':      'contract_symbol, qty=1',
            'fn':          buy_option_contract,
        },
    }


@click.command('automate-options')
@click.argument('goal', required=False, default=None)
@click.option('--execute', is_flag=True, default=False,
              help='Allow paper options orders via Alpaca.')
@click.option('--budget', default=500.0, show_default=True, type=float)
@click.option('--max-steps', default=10, show_default=True, type=int)
@click.option('--model', 'primary_model', default=MODELS[0],
              type=click.Choice(MODELS))
@click.option('--tickers', default='AAPL,MSFT,NVDA,TSLA,AMZN', show_default=True,
              help='Comma-separated tickers to screen for options plays.')
def automate_options(goal, execute, budget, max_steps, primary_model, tickers):
    """AI agent that identifies and paper-trades options contracts.

    \b
    Examples:
      buffet-bot automate-options
      buffet-bot automate-options "find the best call option this week" --execute
      buffet-bot automate-options --tickers NVDA,AMD,INTC --execute --budget 1000
    """
    if not ensure_ollama_running():
        return
    ticker_list = [t.strip().upper() for t in tickers.split(',') if t.strip()]
    if not goal:
        goal = (
            f"Analyze {', '.join(ticker_list)} for directional options plays. "
            f"Buy the highest-conviction call or put contract within ${budget:.2f}."
        )

    from buffet_bot.automate import OPTIONS_AGENT_PROMPT
    tools = _build_options_tools(
        execute=execute, budget=budget, primary_model=primary_model,
        ticker_list=ticker_list,
    )

    console.print(Panel(
        f"[bold]Goal:[/bold] {goal}\n"
        f"[bold]Budget:[/bold] ${budget:,.2f}  "
        f"[bold]Tickers:[/bold] {', '.join(ticker_list)}  "
        f"[bold]Mode:[/bold] {'[green]EXECUTE[/green]' if execute else '[yellow]DRY RUN[/yellow]'}  "
        f"[bold]Max steps:[/bold] {max_steps}  "
        f"[bold]Model:[/bold] {primary_model}",
        title="[bold cyan]Buffet-Bot Automate Options[/bold cyan]",
        border_style="cyan",
    ))

    run_agent_loop(
        goal, tools,
        model=primary_model, budget=budget, max_steps=max_steps,
        execute=execute, console=console, ollama_module=_ollama,
        risk='high', strategy='speculative',
        system_prompt_template=OPTIONS_AGENT_PROMPT,
    )

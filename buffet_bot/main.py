"""Buffet-Bot CLI — entry point. Imports all commands and registers them with Click."""
import click

from buffet_bot.db import (
    init_db,
    log_recommendation, get_recent_recommendations,
    add_to_watchlist, remove_from_watchlist, get_watchlist,
    create_alert, get_alerts, delete_alert, mark_alert_triggered,
    log_earnings_result, get_earnings_history,
)
from buffet_bot.globals import DB_PATH  # re-exported for tests that patch buffet_bot.main.DB_PATH

# ── Trading commands ──────────────────────────────────────────────────────────
from buffet_bot.cmd_trading import (
    ask, lookup, browse, analyze, buy,
    history, portfolio, chat, scan, status,
    stream, chart, dashboard, compare, explain,
)

# ── Intel commands ────────────────────────────────────────────────────────────
from buffet_bot.cmd_intel import (
    news, insiders, crypto, volatile, options,
)

# ── Portfolio commands ────────────────────────────────────────────────────────
from buffet_bot.cmd_portfolio import (
    rebalance, backtest, correlate, check_sells, var,
    forecast, whatif, scenarios, milestones, sectors, compound,
)

# ── Account commands ──────────────────────────────────────────────────────────
from buffet_bot.cmd_account import (
    guide, plans, automate, automate_crypto, automate_options,
    config, alerts, watchlist, beats, completion,
)


@click.group()
@click.version_option()
def cli():
    """Buffet-Bot the AI Trading Bot CLI - Local LLM Powered"""
    pass


# Register trading commands
cli.add_command(ask)
cli.add_command(lookup)
cli.add_command(browse)
cli.add_command(analyze)
cli.add_command(buy)
cli.add_command(history)
cli.add_command(portfolio)
cli.add_command(chat)
cli.add_command(scan)
cli.add_command(status)
cli.add_command(stream)
cli.add_command(chart)
cli.add_command(dashboard)
cli.add_command(compare)
cli.add_command(explain)

# Register intel commands
cli.add_command(news)
cli.add_command(insiders)
cli.add_command(crypto)
cli.add_command(volatile)
cli.add_command(options)

# Register portfolio commands
cli.add_command(rebalance)
cli.add_command(backtest)
cli.add_command(correlate)
cli.add_command(check_sells)
cli.add_command(var)
cli.add_command(forecast)
cli.add_command(whatif)
cli.add_command(scenarios)
cli.add_command(milestones)
cli.add_command(sectors)
cli.add_command(compound)

# Register account commands
cli.add_command(guide)
cli.add_command(plans)
cli.add_command(automate)
cli.add_command(automate_crypto)
cli.add_command(automate_options)
cli.add_command(config)
cli.add_command(alerts)
cli.add_command(watchlist)
cli.add_command(beats)
cli.add_command(completion)

init_db()


def main():
    cli()

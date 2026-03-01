# Coding Patterns — Buffet-Bot

> Owned by: Architect Agent
> All engineers must follow these patterns. Verify each pattern still holds by reading `main.py` before use.

---

## 1. CLI Command Declaration

Every command uses Click decorators on `cli` (the root group). Always uppercase the ticker argument immediately.

```python
@cli.command()
@click.argument('ticker')
@click.option('--risk', default='medium', type=click.Choice(['low', 'medium', 'high']),
              show_default=True, help='Risk tolerance level.')
@click.option('--model', default=MODELS[0], help='Primary Ollama model to use.')
@click.option('--strategy', default='value', type=click.Choice(list(STRATEGY_PROMPTS)),
              show_default=True, help='Investment strategy lens.')
def my_command(ticker, risk, model, strategy):
    """One-line docstring — shown in `--help` output."""
    ticker = ticker.upper()
    # implementation
```

**Rules:**
- Docstring is mandatory — it appears in `--help`
- Ticker argument always uppercased on first line of body
- Use `click.Choice(...)` for any option with fixed allowed values
- Use `show_default=True` for options with meaningful defaults

---

## 2. All Output via `console.print()`

**Never use `print()`.** All terminal output goes through the Rich `Console` instance.

```python
console = Console()   # module-level singleton

# Good
console.print("[bold green]Done.[/bold green]")
console.print(Panel("content", title="Title", border_style="cyan"))

# Bad — never do this
print("Done.")
```

---

## 3. Panel Output

Panels are the primary output container for any meaningful block of information.

```python
# Standard panel
console.print(Panel(
    "content string with [bold]Rich markup[/bold]",
    title="[bold]Panel Title[/bold]",
    border_style="cyan",        # use semantic color (see color table)
))

# Multi-line panel content — use \n, not nested panels
content = (
    f"Price:  [bold]${price:.2f}[/bold]\n"
    f"Change: [{color}]{pct:+.2f}%[/{color}]"
)
console.print(Panel(content, title="Live Market", border_style="green"))
```

**Color semantics for `border_style`:**
| Meaning | Color |
|---------|-------|
| Live / positive data | `green` |
| AI model output | `cyan` (deepseek-r1) / `magenta` (qwen2.5:7b) |
| Info / neutral | `cyan` |
| Warning / HOLD | `yellow` |
| Error / SELL | `red` |
| Wizard / interactive | `cyan` |

---

## 4. Table Output

```python
from rich.table import Table
from rich import box

table = Table(box=box.ROUNDED, header_style="bold cyan")
table.add_column("Ticker", style="bold", no_wrap=True)
table.add_column("Score", justify="right")
table.add_column("Action")

for row_data in results:
    color = "green" if row_data["action"] == "BUY" else "red"
    table.add_row(
        row_data["ticker"],
        str(row_data["score"]),
        f"[{color}]{row_data['action']}[/{color}]",
    )

console.print(table)
```

**Rules:**
- `box.ROUNDED` for primary data tables
- `box.SIMPLE` for dense secondary tables (e.g., inside panels, news lists)
- Numeric columns: `justify="right"`
- Text columns: `justify="left"` (default)
- `no_wrap=True` on ticker/symbol columns

---

## 5. LLM Query Pattern

Two variants — JSON-structured response and freeform text response.

### JSON response (trading decisions, sentiment scoring)
```python
try:
    resp = ollama.chat(
        model=model,
        messages=[{'role': 'user', 'content': prompt}],
        options={'temperature': 0.2},   # low temp for structured output
    )
    advice_str = resp['message']['content'].strip()
    # Handle models that add preamble before the JSON
    if advice_str.startswith('{'):
        advice = json.loads(advice_str)
    else:
        start, end = advice_str.find('{'), advice_str.rfind('}') + 1
        advice = json.loads(advice_str[start:end]) if start >= 0 else {'error': 'no JSON'}
    # Always validate keys before using them
    action = advice.get('action', 'HOLD')
    confidence = float(advice.get('confidence', 0.0))
except json.JSONDecodeError:
    advice = {'error': 'Invalid JSON', 'raw': resp['message']['content']}
except Exception as e:
    advice = {'error': str(e)}
```

### Freeform response (ask, chat, explanations)
```python
try:
    resp = ollama.chat(
        model=model,
        messages=[{'role': 'user', 'content': prompt}],
        options={'temperature': 0.5},   # higher temp for creative/conversational
    )
    text = resp['message']['content'].strip()
except Exception as e:
    text = f"Error: {e}"
```

**Rules:**
- Always wrap ollama calls in `try/except Exception as e`
- Always use `.get(key, default)` — never assume a key exists in the response dict
- Temperature: `0.1–0.2` for decisions/JSON, `0.5` for freeform
- Never use `format='json'` parameter — it's not used in this codebase (rely on prompt engineering)

---

## 6. Multi-Model Query Loop

When querying both models:

```python
models_to_query = [primary_model]
if primary_model != MODELS[1]:          # avoid querying qwen2.5:7b twice
    models_to_query.append(MODELS[1])

responses = {}
for model in models_to_query:
    try:
        resp = ollama.chat(model=model, messages=[...], options={...})
        responses[model] = json.loads(resp['message']['content'].strip())
    except Exception as e:
        responses[model] = {'error': str(e)}
```

**Consensus vote:**
```python
actions = [r.get('action', 'HOLD') for r in responses.values()
           if isinstance(r, dict) and 'action' in r]
consensus = max(set(actions), key=actions.count) if actions else 'HOLD'
```

---

## 7. Data Fetching with Fallback Chain

External data calls follow: **Alpaca → yfinance → empty dict**. Never crash on data fetch failure.

```python
def get_some_data(ticker: str) -> dict:
    # Attempt 1: Alpaca (preferred — real-time)
    try:
        req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        quote = data_client.get_stock_latest_quote(req)[ticker]
        return {'price': float(quote.ask_price), 'source': 'alpaca'}
    except Exception:
        pass  # fall through to next source

    # Attempt 2: yfinance (fallback)
    try:
        fi = yf.Ticker(ticker).fast_info
        return {'price': float(fi.last_price), 'source': 'yfinance'}
    except Exception as e:
        console.print(f"[dim red]Data unavailable for {ticker}: {e}[/dim red]")

    # Attempt 3: empty dict — caller must handle missing keys with .get()
    return {}
```

**Rules:**
- Always `return {}` on total failure — never `return None`
- Callers always use `.get('key', fallback)` — never `dict['key']`
- Log fallback failures with `[dim red]` — not full `[red]` (non-critical)

---

## 8. Error Display

```python
# User-facing error (critical — command cannot continue)
console.print(f"[red]Error: {e}[/red]")
return

# Non-critical warning (data unavailable but command continues)
console.print(f"[dim red]Live quote unavailable for {ticker}: {e}[/dim red]")

# Informational dim text (background status)
console.print(f"[dim]Fetching data for {ticker}...[/dim]")
```

---

## 9. Printing AI Model Responses

Use the shared `_print_ai_responses()` helper — do not inline this pattern.

```python
# In _print_ai_responses() (already in main.py):
for model, resp in responses.items():
    color = MODEL_COLORS.get(model, 'white')
    content = json.dumps(resp, indent=2) if isinstance(resp, dict) else str(resp)
    console.print(Panel(content,
                        title=f"[bold {color}]{model}[/bold {color}]",
                        border_style=color))

# Usage — call the helper, don't repeat it:
_print_ai_responses(result['responses'])
```

---

## 10. Consensus Text Formatting

Use the shared `_consensus_text()` helper.

```python
# In _consensus_text() (already in main.py):
def _consensus_text(consensus):
    color = {'BUY': 'green', 'SELL': 'red', 'HOLD': 'yellow'}.get(consensus, 'white')
    return f"[bold {color}]{consensus}[/bold {color}]"

# Usage:
console.print(f"\nConsensus: {_consensus_text(result['consensus'])}")
```

---

## 11. Plotext Chart Pattern

```python
import plotext as plt

plt.clear_figure()
plt.plot(x_values, y_values, color='cyan', label='Portfolio')
plt.plot(x_values, bench_values, color='red', label='SPY')
plt.title("Equity Curve")
plt.xlabel("Days")
plt.ylabel("Value ($)")
plt.grid(True, True)
plt.show()
```

**Rules:**
- Always call `plt.clear_figure()` first — plotext retains state between calls
- Always set `title`, `xlabel`, `ylabel`
- Primary series: `color='cyan'`, benchmark/comparison: `color='red'`
- Always call `plt.grid(True, True)`

---

## 12. Buffett Score Display

```python
score = buffett.get('score', 0)
score_color = 'green' if score >= 70 else ('yellow' if score >= 40 else 'red')
console.print(f"Buffett Score: [{score_color}]{score}/100[/{score_color}]")
```

---

## 13. User Confirmation Pattern

```python
# Rich Prompt (for multi-choice)
from rich.prompt import Prompt
choice = Prompt.ask(
    "Execute orders?",
    choices=['all', 'pick', 'skip'],
    default='skip',
)

# Click confirm (for yes/no)
import click
if click.confirm(f"Execute BUY {qty}x {ticker}? (Paper)", default=False):
    _place_order(ticker, best_resp)
```

---

## 14. New Package Dependencies

When adding a new `import`, update **both**:

```
requirements.txt        ← add: package-name>=x.y.z
pyproject.toml          ← add to [project.dependencies]: "package-name>=x.y.z"
```

Then update `README.md` prerequisites table if the package is user-visible.

---

## 15. CLI Subgroup Pattern

Use Click's `@cli.group()` when a command has sub-commands (e.g., `watchlist add`, `watchlist remove`, `watchlist show`).

```python
@cli.group()
def watchlist():
    """One-line docstring shown in top-level --help."""
    pass

@watchlist.command('add')
@click.argument('ticker')
def watchlist_add(ticker):
    """Add a ticker: buffet-bot watchlist add TSLA"""
    ticker = ticker.upper()
    # implementation

@watchlist.command('remove')
@click.argument('ticker')
def watchlist_remove(ticker):
    """Remove a ticker: buffet-bot watchlist remove TSLA"""
    ticker = ticker.upper()
    # implementation

@watchlist.command('show')
def watchlist_show():
    """Show all tickers: buffet-bot watchlist show"""
    # implementation
```

**Rules:**
- The group function body must be `pass` — no logic
- Each sub-command is decorated with `@<group_name>.command('<subcommand>')`, not `@cli.command()`
- Sub-command function names use `<group>_<subcommand>` to avoid name collisions (e.g., `watchlist_add`)
- String name in `@watchlist.command('add')` is the CLI-facing name — use lowercase with hyphens for multi-word sub-commands
- Ticker arguments still uppercase on first line of body — same rule as top-level commands

---

## Anti-Patterns (Never Do These)

| Anti-pattern | Correct approach |
|-------------|-----------------|
| `print("text")` | `console.print("text")` |
| `dict['key']` on LLM response | `dict.get('key', fallback)` |
| `paper=False` on TradingClient | Never — paper=True is hardcoded |
| Nested try/except hiding real errors | Catch at boundary, log with `[red]`, return fallback |
| `plt.show()` without `plt.clear_figure()` first | Always clear first |
| `return None` from a data fetch function | `return {}` so callers can safely call `.get()` |
| Adding OpenAI/Anthropic API calls | Local Ollama only |
| Creating new `.py` files without Architect approval | All logic stays in `main.py` until split is approved |

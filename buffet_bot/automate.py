"""
buffet_bot/automate.py — ReAct-style agent loop for the automate command.

No imports from main.py (avoids circular import). Caller injects `ollama`
module and `console` as parameters.
"""
import re
import json
import time

MAX_STEPS_DEFAULT  = 10
BUDGET_DEFAULT     = 500.0
TEMPERATURE        = 0.2
MAX_HISTORY_PAIRS  = 3  # keep system + user-goal + this many exchange pairs = 8 messages max

AGENT_SYSTEM_PROMPT = """
You are an autonomous investing agent using Warren Buffett's value principles.
Goal: {goal}
Budget: ${budget:.2f} | Mode: {execute_mode}
Risk appetite: {risk} | Strategy: {strategy}

Available tools:
{tool_descriptions}

Each turn respond with ONLY one JSON object (no prose, no markdown):
  {{"thought": "<brief reasoning>", "tool": "<tool_name>", "args": {{...}}}}

When done, call: {{"thought": "...", "tool": "done", "args": {{"summary": "..."}}}}
If a tool errors, adapt (try a different ticker). Never fabricate data.
{execute_directive}
""".strip()

EXECUTE_DIRECTIVE = (
    "IMPORTANT: You have permission to place paper trades. "
    "When analyze_stock() returns consensus='BUY' and budget remains, call buy_stock() immediately."
)

CRYPTO_AGENT_PROMPT = """
You are an autonomous crypto trading agent.
Goal: {goal}
Budget: ${budget:.2f} | Mode: {execute_mode}
Risk appetite: {risk}

Available tools:
{tool_descriptions}

Each turn respond with ONLY one JSON object:
  {{"thought": "<brief reasoning>", "tool": "<tool_name>", "args": {{...}}}}

When done, call: {{"thought": "...", "tool": "done", "args": {{"summary": "..."}}}}
Workflow: scan_cryptos → analyze the top movers → buy_crypto if confident.
{execute_directive}
""".strip()

OPTIONS_AGENT_PROMPT = """
You are an autonomous options trading agent.
Goal: {goal}
Budget: ${budget:.2f} | Mode: {execute_mode}
Risk appetite: {risk}

Available tools:
{tool_descriptions}

Options rules:
- Call options → when stock direction is BUY (bullish)
- Put options → when stock direction is SELL (bearish)
- Prefer ATM or 1-strike OTM contracts, nearest 30–45 day expiry
- Each contract controls 100 shares; premium is typically $50–$500
- Never buy a contract if IV > 80% without strong catalyst reason

Workflow: analyze_stock_direction → get_options_chain (correct side) → buy_option_contract.
Each turn respond with ONLY one JSON object:
  {{"thought": "<brief reasoning>", "tool": "<tool_name>", "args": {{...}}}}
When done: {{"thought": "...", "tool": "done", "args": {{"summary": "..."}}}}
{execute_directive}
""".strip()


SWEEP_AGENT_PROMPT = """
You are a deterministic portfolio sweep agent executing a structured investing run.
Goal: {goal}
Budget: ${budget:.2f} | Mode: {execute_mode}
Risk appetite: {risk} | Strategy: {strategy}

You MUST follow this exact workflow — do not deviate:
  Step 1: Call scan_stocks(top=10) to find the highest Buffett-scored candidates.
  Step 2: For each top candidate (up to 5), call analyze_stock(ticker=...) to confirm BUY signal.
  Step 3: For each confirmed BUY, call buy_stock(ticker=..., qty=...) within budget.
  Step 4: Call done(summary=...) with a brief report of what was bought and total deployed.

Rules:
- Only buy if analyze_stock returns consensus='BUY' and confidence >= 0.6.
- Never exceed the total budget across all buy_stock calls.
- If no BUY candidates are found after scanning, call done() immediately.
- Do not repeat analyze_stock on the same ticker.

Available tools:
{tool_descriptions}

Each turn respond with ONLY one JSON object:
  {{"thought": "<brief reasoning>", "tool": "<tool_name>", "args": {{...}}}}

When done: {{"thought": "...", "tool": "done", "args": {{"summary": "..."}}}}
{execute_directive}
""".strip()


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from LLM output, handling common noise."""
    # Strip deepseek-r1 <think>...</think> reasoning blocks
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    # Strip markdown code fences
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting the outermost {...} substring
    start = text.find('{')
    end   = text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _format_tool_descriptions(tools: dict) -> str:
    """Return a bullet-list string describing each tool."""
    lines = []
    for name, meta in tools.items():
        params = meta.get('params', '')
        desc   = meta.get('description', '')
        lines.append(f"  {name}({params}) — {desc}")
    return "\n".join(lines)


def run_agent_loop(
    goal: str,
    tools: dict,
    *,
    model: str,
    budget: float,
    max_steps: int,
    execute: bool,
    console,
    ollama_module,
    risk: str = 'medium',
    strategy: str = 'value',
    system_prompt_template: str | None = None,
) -> dict:
    """
    Drive the ReAct agent loop until the LLM calls done() or max_steps is hit.

    Parameters
    ----------
    goal          : natural-language goal string
    tools         : {tool_name: {"description": str, "params": str, "fn": callable}}
    model         : Ollama model name
    budget        : dollar cap for buy_stock calls
    max_steps     : hard iteration limit
    execute       : whether real orders are allowed
    console       : Rich Console (injected to avoid circular import)
    ollama_module : the `ollama` module (injected)
    risk          : user risk appetite ('low', 'medium', 'high')
    strategy      : investing strategy ('value', 'growth', 'dividend', 'turnaround')

    Returns
    -------
    {"summary": str, "steps_taken": int, "timed_out": bool}
    """
    from rich.panel import Panel
    from rich import box

    execute_mode      = "EXECUTE (paper trades allowed)" if execute else "DRY RUN (no orders placed)"
    execute_directive = EXECUTE_DIRECTIVE if execute else ""
    prompt_template = system_prompt_template or AGENT_SYSTEM_PROMPT
    system_content = prompt_template.format(
        goal=goal,
        budget=budget,
        execute_mode=execute_mode,
        risk=risk,
        strategy=strategy,
        execute_directive=execute_directive,
        tool_descriptions=_format_tool_descriptions(tools),
    )

    messages = [
        {"role": "system",  "content": system_content},
        {"role": "user",    "content": f"Begin working toward the goal: {goal}"},
    ]

    summary      = ""
    timed_out    = False
    loop_start   = time.perf_counter()

    for step in range(1, max_steps + 1):
        # ── Trim message history (Task #8): keep system + last MAX_HISTORY_PAIRS exchanges ──
        max_len = 2 + 2 * MAX_HISTORY_PAIRS
        if len(messages) > max_len:
            messages[2:] = messages[-(2 * MAX_HISTORY_PAIRS):]

        # ── Query LLM ────────────────────────────────────────────────────────
        step_start = time.perf_counter()
        with console.status(f"[cyan]Agent thinking (step {step}/{max_steps})...[/cyan]"):
            try:
                resp = ollama_module.chat(
                    model=model,
                    messages=messages,
                    options={"temperature": TEMPERATURE},
                )
                raw_content = resp["message"]["content"].strip()
            except Exception as e:
                console.print(f"[red]LLM error on step {step}: {e}[/red]")
                break

        # ── Parse JSON ───────────────────────────────────────────────────────
        parsed = _extract_json(raw_content)
        if parsed is None:
            hint = (
                "Your last response could not be parsed as JSON. "
                "Reply with ONLY a single JSON object — no prose, no markdown fences. "
                f"Example: {{\"thought\": \"...\", \"tool\": \"scan_stocks\", \"args\": {{\"top\": 5}}}}"
            )
            messages.append({"role": "assistant", "content": raw_content})
            messages.append({"role": "user",      "content": hint})
            console.print(f"[yellow]Step {step}: JSON parse failed — injecting hint and retrying.[/yellow]")
            continue

        thought    = parsed.get("thought", "")
        tool_name  = parsed.get("tool", "")
        tool_args  = parsed.get("args", {})

        # ── Done? ─────────────────────────────────────────────────────────────
        if tool_name == "done":
            summary = tool_args.get("summary", thought or "Agent completed.")
            console.print(Panel(
                summary,
                title="[bold green]Agent Complete[/bold green]",
                border_style="green",
            ))
            break

        # ── Step panel ───────────────────────────────────────────────────────
        step_elapsed   = time.perf_counter() - step_start
        total_elapsed  = time.perf_counter() - loop_start
        args_str = json.dumps(tool_args)
        console.print(Panel(
            f"[bold]Thought:[/bold] {thought}\n"
            f"[bold]Tool:[/bold]    {tool_name}   "
            f"[dim]Args: {args_str}[/dim]\n"
            f"[dim]Step: {step_elapsed:.1f}s | Total: {total_elapsed:.1f}s[/dim]",
            title=f"[dim]Agent Step {step}[/dim]",
            border_style="dim",
            box=box.SIMPLE,
        ))

        # ── Execute tool ─────────────────────────────────────────────────────
        if tool_name not in tools:
            tool_result = {"error": f"Unknown tool '{tool_name}'. Available: {list(tools.keys())}"}
        else:
            try:
                tool_result = tools[tool_name]["fn"](**tool_args)
            except TypeError as e:
                tool_result = {"error": f"Bad arguments for '{tool_name}': {e}"}
            except Exception as e:
                tool_result = {"error": str(e)}

        # ── Serialize & display result ────────────────────────────────────────
        result_json = json.dumps(tool_result)
        display_str = result_json[:300] + ("…" if len(result_json) > 300 else "")
        console.print(f"  [dim]Tool result:[/dim] {display_str}")

        # Truncate for history to avoid context bloat
        history_str = result_json[:500] + ("…" if len(result_json) > 500 else "")

        messages.append({"role": "assistant", "content": raw_content})
        messages.append({"role": "user",      "content": history_str})

    else:
        # Loop exhausted without break
        timed_out = True
        console.print(Panel(
            f"[yellow]Agent stopped after {max_steps} steps without calling done().[/yellow]",
            title="[yellow]Max Steps Reached[/yellow]",
            border_style="yellow",
        ))

    return {
        "summary":     summary,
        "steps_taken": step,
        "timed_out":   timed_out,
    }

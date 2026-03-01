# Architectural Decision Log — Buffet-Bot

> Owned by: Architect Agent + Product Manager Agent
> Purpose: Record major decisions with their rationale so future agents don't re-litigate them.
> Format: Most recent decisions at the top.

---

## How to Add an Entry

```markdown
## ADR-NNN: Short decision title
- **Date:** YYYY-MM-DD
- **Status:** Accepted | Superseded by ADR-NNN | Deprecated
- **Decided by:** [agent role or user]

**Context:** What was the situation that forced a decision?
**Decision:** What was chosen?
**Rationale:** Why this over the alternatives?
**Consequences:** What does this enable or constrain going forward?
```

---

## ADR-007: Amnesia clause for all agents
- **Date:** 2026-02-28
- **Status:** Accepted
- **Decided by:** User + Product Manager

**Context:** Cross-session memory files (auto-memory) were found to cause agents to act on stale assumptions about function names, command counts, and architecture that no longer matched the actual code.

**Decision:** Every agent `.md` file includes an amnesia clause requiring the agent to read source files from disk at the start of each session before taking any action.

**Rationale:** The codebase changes faster than memory files are updated. Stale memory is worse than no memory because it creates false confidence. Reading the actual files takes seconds and is always accurate.

**Consequences:** Agents spend the first part of each session reading `main.py`. This is intentional and expected. Do not skip this step to "save time."

---

## ADR-006: Multi-agent system via per-role `.md` instruction files
- **Date:** 2026-02-28
- **Status:** Accepted
- **Decided by:** User

**Context:** Building Buffet-Bot to maximum capacity requires parallel work across different specializations: product, engineering, architecture, UI, and data sourcing. A single-agent approach serializes work that could be parallel.

**Decision:** Each role gets its own Claude Code instruction file in `agents/`. Agents coordinate through shared files on disk (`ROADMAP.md`, `PATTERNS.md`, `SCHEMA.md`, etc.) rather than live communication.

**Rationale:** File-based coordination is durable, reviewable, and doesn't require a live multi-agent orchestration system. Each agent is a standalone Claude Code session with a specialized system prompt.

**Consequences:** Agents must write their outputs to shared files for other agents to consume. The Product Manager owns the coordination layer. Engineers must check `PATTERNS.md` before implementing to avoid conflicting approaches.

---

## ADR-005: Intentional monolith — all logic in `buffet_bot/main.py`
- **Date:** (estimated v0.1.0)
- **Status:** Accepted (re-review at >3000 lines)
- **Decided by:** Original developer

**Context:** The project started as a simple CLI script and grew to ~2100 lines. There were multiple opportunities to split into modules.

**Decision:** Keep all logic in a single `buffet_bot/main.py` file until there is a concrete, justified reason to split.

**Rationale:** Monoliths are simpler to navigate, edit, and reason about for a solo/small-team project at this stage. Module splits introduce import complexity, circular dependency risks, and coordination overhead that aren't justified yet.

**Consequences:** The Architect must actively monitor line count. When any logical domain (e.g., backtesting) exceeds ~500 lines and grows independently, raise a split proposal. Approved split structure is documented in `architect.md`.

---

## ADR-004: Paper trading only — `paper=True` hardcoded, not configurable
- **Date:** (estimated v0.1.0)
- **Status:** Accepted — permanent constraint
- **Decided by:** Original developer

**Context:** Buffet-Bot makes autonomous trading decisions driven by LLMs. Allowing real-money execution would create serious financial risk for users.

**Decision:** `TradingClient(API_KEY, SECRET_KEY, paper=True)` is hardcoded. There is no flag, config option, or environment variable to switch to live trading.

**Rationale:** LLM-driven trading without human oversight is inappropriate for real capital. The paper trading constraint is a safety guarantee, not a limitation.

**Consequences:** This decision is **permanent and non-negotiable**. Any agent or pull request that changes `paper=True` to `paper=False` must be rejected. The README and security note make this explicit.

---

## ADR-003: Local LLMs via Ollama only — no cloud AI API calls
- **Date:** (estimated v0.1.0)
- **Status:** Accepted
- **Decided by:** Original developer

**Context:** Many trading assistants require OpenAI or Anthropic API subscriptions, creating recurring costs and data privacy concerns (sending financial data to external servers).

**Decision:** All LLM inference runs locally via Ollama. No OpenAI, Anthropic, Gemini, or other cloud AI API keys are used. The models are `deepseek-r1` and `qwen2.5:7b`.

**Rationale:** Local inference is free after one-time model download, preserves user data privacy, and works offline. This is a core differentiator of Buffet-Bot.

**Consequences:** New features cannot add cloud LLM calls. If a feature needs a capability that local models can't provide, find a different approach (deterministic algorithm, free public API, etc.).

---

## ADR-002: Free-tier data sources only
- **Date:** (estimated v0.1.0)
- **Status:** Accepted
- **Decided by:** Original developer

**Context:** Buffet-Bot targets individual investors who should not need to pay for data subscriptions on top of a brokerage account.

**Decision:** All data sources must have a usable free tier. Current approved sources: `yfinance` (free), Alpaca Data API (free with paper account), Alpaca News API (free with paper account).

**Rationale:** Paid data subscriptions (Bloomberg, Refinitiv, etc.) are cost-prohibitive for the target user. Free-tier sources provide sufficient data for Buffett-style fundamental analysis.

**Consequences:** The Web Scraper agent must evaluate rate limits and free tier viability before recommending any new data source. No source requiring a credit card for free tier is acceptable without explicit user approval.

---

## ADR-001: SQLite for local persistence — no external database
- **Date:** (estimated v0.2.0)
- **Status:** Accepted
- **Decided by:** Original developer

**Context:** Buffet-Bot needed persistent storage for recommendation history and outcomes without requiring users to set up a database server.

**Decision:** Use Python's built-in `sqlite3` module. Database file lives at `~/.buffet-bot.db`. Tables: `recommendations`, `outcomes`.

**Rationale:** SQLite requires no setup, is file-based, travels with the user's home directory, and handles the expected volume (hundreds to thousands of rows) without any performance concerns. No ORM is needed at this scale.

**Consequences:** Schema changes must be backwards-compatible using `ALTER TABLE ... ADD COLUMN`. The `init_db()` function handles migration idempotently via `CREATE TABLE IF NOT EXISTS`. See `SCHEMA.md` for full schema reference.

---

## ADR-009: Config file format — TOML at `~/.buffet-bot-config.toml`
- **Date:** 2026-02-28
- **Status:** Accepted (pending PM approval to implement)
- **Decided by:** Architect Agent

**Context:** ROADMAP v0.4.1 lists `~/.buffet-bot-config.toml` for user preferences. The open question in this file listed `.toml` vs `.json` vs more env vars.

**Decision:** Use TOML (via Python stdlib `tomllib` / `tomli` write shim) at `~/.buffet-bot-config.toml`.

**Rationale:**
- `tomllib` is stdlib in Python 3.11+ (read-only). For writing, `tomli-w` is a small, single-purpose package.
- TOML is more human-readable than JSON for config files — inline comments, no trailing comma errors.
- `.env` stays for secrets only (API keys). Config file is for user preferences only — no secrets.
- JSON is already used for plan files (`~/.buffet-plans/`) — having a separate format for config reduces confusion about what each file type contains.

**Config schema (proposed):**
```toml
# ~/.buffet-bot-config.toml

[defaults]
model    = "deepseek-r1"    # Primary Ollama model
risk     = "medium"          # low | medium | high
strategy = "value"           # value | growth | dividend | turnaround

[display]
buffett_score_green  = 70    # Score >= this → green
buffett_score_yellow = 40    # Score >= this → yellow (else red)
```

**Consequences:**
- ENG must add `tomli-w>=1.0.0` to `requirements.txt` and `pyproject.toml`
- Load config with `_load_config()` helper at module level (after `load_dotenv()`). Falls back to hardcoded defaults if file absent.
- Never store secrets in config — only in `.env`
- Config path: `CONFIG_PATH = os.path.expanduser("~/.buffet-bot-config.toml")`
- `_load_config()` must be silent on missing file (return defaults dict, not raise)

---

## ADR-008: `_analyze_crypto` migration to `crypto.py`
- **Date:** 2026-02-28
- **Status:** Proposed (pending PM approval)
- **Decided by:** Architect Agent

**Context:** `_analyze_crypto()` (lines 1204–1334, 131 lines) lives in `main.py` but is logically crypto domain logic. All other crypto code lives in `buffet_bot/crypto.py`.

**Decision:** Move `_analyze_crypto()` to `crypto.py`. The `crypto` CLI command in `main.py` calls it via import.

**Rationale:** Keeps crypto logic co-located, reduces `main.py` by ~131 lines (delays hitting the 3000-line threshold), and is consistent with how `volatile.py` and `politicians.py` own their domain logic.

**Implementation requirements:**
- `_analyze_crypto()` uses `console`, `MODELS`, `MODEL_COLORS`, `STRATEGY_PROMPTS`, `ollama`, `json`, `TradingClient` — these must be passed as parameters or the function must import them from a shared constants module.
- Simplest approach: pass `console`, `models_list`, `model_colors`, `strategy_prompts` as parameters. Avoids circular imports.
- The `crypto` CLI command in `main.py` becomes a thin wrapper calling `crypto.analyze_crypto(symbol, dry_run, primary_model, console, MODELS, MODEL_COLORS, STRATEGY_PROMPTS)`.

**Consequences:** `crypto.py` grows by ~131 lines but remains within a single domain. `main.py` shrinks by same amount.

---

## Open Questions (Undecided)

These are decisions that haven't been made yet. The Product Manager should facilitate decisions on these when they become relevant.

| Question | Context | Status |
|----------|---------|--------|
| When to split `main.py` into modules | Now 2760 lines; threshold 3000 | **Decided:** wait until 3000 lines; pre-approved split plan in AUDIT.md |
| Config file format | User preferences (model, risk, strategy) | **Decided:** TOML at `~/.buffet-bot-config.toml` — see ADR-009 |
| PyPI distribution | Making `pip install buffet-bot` work | Open — needs version tagging strategy |
| Multi-model expansion | Users want `llama3`, `mistral`, etc. | Open — MODELS list is hardcoded; no decision made |
| Async LLM queries | Two models queried sequentially — slow | Open — `asyncio` import exists but unused; remove until decided (see D-001 in AUDIT.md) |

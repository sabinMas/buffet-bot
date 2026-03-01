# Multi-Agent System — Buffet-Bot

This directory contains instruction files for running multiple specialized Claude Code agents in parallel on the Buffet-Bot project.

---

## Agent Roster

| File | Agent Role | Primary Focus |
|------|-----------|---------------|
| `product-manager.md` | Product Manager | Roadmap, specs, coordination, quality gates |
| `software-engineer.md` | Software Engineer | Feature implementation in `buffet_bot/main.py` |
| `architect.md` | Systems Architect | Module structure, DB schema, patterns, tech debt |
| `stylist.md` | UI/UX Stylist | Rich terminal output, colors, charts, visual polish |
| `web-scraper.md` | Data Scout | Finding free investment APIs, integration blueprints |

---

## How to Run Multiple Agents

Each agent is a separate Claude Code session pointed at a different instruction file. Open multiple terminal windows:

```bash
# Terminal 1 — Product Manager
claude --system-prompt agents/product-manager.md

# Terminal 2 — Software Engineer
claude --system-prompt agents/software-engineer.md

# Terminal 3 — Architect
claude --system-prompt agents/architect.md

# Terminal 4 — Stylist
claude --system-prompt agents/stylist.md

# Terminal 5 — Data Scout
claude --system-prompt agents/web-scraper.md
```

> **Tip:** Each agent has an amnesia clause — it reads the source files fresh each session. This ensures agents don't act on stale assumptions. Let each agent read `buffet_bot/main.py` before giving it instructions.

---

## Coordination Protocol

1. **Product Manager** sets priorities in `ROADMAP.md`
2. **Architect** reviews structure and writes specs to `AUDIT.md` / `PATTERNS.md`
3. **Data Scout** finds APIs and writes blueprints to `DATA-SOURCES.md` / `INTEGRATION-TICKETS.md`
4. **Software Engineer** implements features from specs, following `PATTERNS.md`
5. **Stylist** polishes output after features are implemented, referencing `UI-AUDIT.md`

---

## Shared Files (All Agents Read These)

| File | Purpose |
|------|---------|
| `buffet_bot/main.py` | Ground truth — all agents read this first |
| `CLAUDE.md` | Project-level rules |
| `README.md` | User-facing documentation |
| `agents/ROADMAP.md` | Prioritized feature backlog |
| `agents/DECISIONS.md` | Architectural decision log |
| `agents/PATTERNS.md` | Coding patterns all engineers must follow |
| `agents/SCHEMA.md` | SQLite schema reference |
| `agents/DATA-SOURCES.md` | Researched APIs and data sources |
| `agents/AUDIT.md` | Structural health report |
| `agents/UI-AUDIT.md` | Visual/UX issues catalog |
| `agents/INTEGRATION-TICKETS.md` | Data integration specs ready for engineering |

---

## Rules All Agents Must Follow

1. **Amnesia first** — read source files before acting on any assumption
2. **Paper trading only** — `paper=True` is hardcoded and must never change
3. **Local LLMs only** — no OpenAI, Anthropic, or cloud AI API calls
4. **Free data only** — no paid API subscriptions without explicit user approval
5. **No real money** — Buffet-Bot is a paper trading tool, always

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
| `qa-engineer.md` | QA / Test Engineer | pytest suite, mocking, regression prevention |
| `performance-engineer.md` | Performance Engineer | Concurrency, async LLM queries, profiling, speed |
| `release-manager.md` | Release Manager | Versioning, PyPI packaging, Docker, CHANGELOG |

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

# Terminal 6 — QA / Test Engineer
claude --system-prompt agents/qa-engineer.md

# Terminal 7 — Performance Engineer
claude --system-prompt agents/performance-engineer.md

# Terminal 8 — Release Manager
claude --system-prompt agents/release-manager.md
```

> **Tip:** Each agent has an amnesia clause — it reads the source files fresh each session. This ensures agents don't act on stale assumptions. Let each agent read `buffet_bot/main.py` before giving it instructions.

---

## Coordination Protocol

1. **Product Manager** sets priorities in `ROADMAP.md`, writes specs, delegates to other agents
2. **Architect** reviews structure and writes specs to `AUDIT.md` / `PATTERNS.md`
3. **Data Scout** finds APIs and writes blueprints to `DATA-SOURCES.md` / `INTEGRATION-TICKETS.md`
4. **Software Engineer** implements features from specs, following `PATTERNS.md`
5. **Stylist** polishes output after features are implemented, referencing `UI-AUDIT.md`
6. **QA / Test Engineer** writes tests after ENG ships a feature; runs regression suite before releases
7. **Performance Engineer** profiles slow commands, resolves async open questions, optimizes concurrency
8. **Release Manager** cuts releases, maintains `CHANGELOG.md`, manages PyPI and Docker packaging

---

## Agent Ownership Map

| Shared File | Primary Owner | Readers |
|-------------|---------------|---------|
| `agents/ROADMAP.md` | Product Manager | All agents |
| `agents/AUDIT.md` | Architect | ENG, PERF, PM |
| `agents/PATTERNS.md` | Architect | ENG, QA |
| `agents/SCHEMA.md` | Architect | ENG, QA |
| `agents/DECISIONS.md` | Architect + PM | All agents |
| `agents/DATA-SOURCES.md` | Data Scout | ENG, PM |
| `agents/INTEGRATION-TICKETS.md` | Data Scout → ENG | PM, ARCH |
| `agents/UI-AUDIT.md` | Stylist | ENG, PM |
| `CHANGELOG.md` | Release Manager | All agents |
| `tests/` directory | QA Engineer | PERF, REL |

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
6. **Token budget awareness** — Each Claude Code session runs on Claude Pro with a ~200K token context window. `main.py` alone consumes ~60–70K tokens to read in full. Reading all agent files on top of that can consume another 40–60K tokens. To prevent sessions from running out of context mid-task:
   - **Scope one atomic deliverable per session** — one ticket, one command, one audit section, one fix. Do not start a second task if the first is not committed.
   - **Prefer targeted reads** — use `offset` and `limit` parameters when reading large files. You do not need to read all of `main.py` for most tasks; read only the relevant section.
   - **Read only the shared files relevant to your current task** — you don't need to read every file in `agents/` every session.
   - **Write your output to shared files as you go** — do not accumulate findings in context and write them all at the end; write incrementally so your work survives a session end.
   - **Commit before context runs low** — a committed partial deliverable is more valuable than a complete but uncommitted one. If you sense you're running low on context, stop, commit what's done, and write a clear handoff note in the relevant shared file.
   - **Write a handoff note if you can't finish** — add a "Progress / Next Steps" section to the relevant shared file (`INTEGRATION-TICKETS.md`, `AUDIT.md`, `ROADMAP.md`) so the next session or agent can continue cleanly.

# Security Audit — Buffet-Bot

> Auditor: Security Auditor Agent
> Date: 2026-03-01
> Scope: v0.4.1 codebase — `buffet_bot/` all modules, `.gitignore`, `requirements.txt`
> Categories covered: Credential Handling, SQL Injection, Shell/Command Injection, Input Validation, Data Exfiltration

---

## Summary

| Finding | Severity | Category | Status |
|---------|----------|----------|--------|
| FINDING-001: Path traversal in plan file management | P1 | Input Validation | [x] Fixed |
| FINDING-002: XML entity expansion (DoS) in insiders.py | P3 | Input Validation | [ ] Open |

**No P0 findings.** All critical checks passed (credentials, SQL, shell injection, data exfiltration).

---

## Passed Checks

| Check | Result |
|-------|--------|
| `.env` excluded from `.gitignore` | ✅ `.gitignore` line 1 |
| No hardcoded API keys in source | ✅ grep: zero hits |
| No credentials printed to console | ✅ grep: only "not configured" hint messages |
| Config file (`~/.buffet-bot-config.toml`) stores no secrets | ✅ Only model/risk/strategy/display thresholds |
| All SQL uses parameterized queries | ✅ All `conn.execute()` calls use `?` placeholders |
| No `subprocess` / `os.system` / `shell=True` | ✅ grep: zero hits across all modules |
| All `--model` flags validated via `click.Choice(MODELS)` | ✅ 10 command definitions checked |
| No cloud LLM calls (OpenAI, Anthropic, Gemini) | ✅ grep: zero hits |
| All LLM calls use local Ollama (`ollama.chat`) | ✅ `automate.py`, `main.py` both inject ollama locally |
| All outbound URLs use HTTPS | ✅ All `requests.get` URLs begin with `https://` |
| Outbound domains limited to approved list | ✅ sec.gov, alpaca.markets, finance.yahoo.com, stlouisfed.org, nasdaq.com, houseofrepresentatives.gov verified |
| FMP API key passed as query param | ✅ Standard for FMP API; not a finding (HTTPS encrypts in transit) |

---

## FINDING-001: Path Traversal in Plan File Management

- **Severity:** P1 (high — fix before v1.0.0)
- **Category:** Input Validation / Path Traversal
- **Files:** `buffet_bot/main.py` lines 799–824, 2163–2165
- **Status:** [x] Fixed 2026-03-01 (Security Auditor, session 4)

### Description

Three functions operate on plan files using user-supplied input (a plan name) joined directly to `PLANS_DIR` with no path confinement check:

1. `_save_plan(name, plan_data)` — **arbitrary file write**
2. `_load_plan(name)` — **arbitrary JSON file read**
3. `plans --delete delete_plan` — **arbitrary `.json` file deletion**

The `name` / `delete_plan` input arrives from:
- CLI option `--run <plan_name>` and `--delete <plan_name>` on the `plans` command
- `Prompt.ask("Plan name")` in the interactive `guide` flow

Neither path is sanitized or confined.

### Evidence

```python
# _save_plan — arbitrary file write
def _save_plan(name, plan_data):
    _ensure_plans_dir()
    path = os.path.join(PLANS_DIR, f"{name}.json")   # ← no containment check
    with open(path, 'w') as f:
        json.dump(plan_data, f, indent=2, default=str)

# plans --delete — arbitrary file delete
path = os.path.join(PLANS_DIR, f"{delete_plan}.json")   # ← no containment check
if os.path.exists(path):
    os.remove(path)
```

**Attack vector:** A plan name of `../../../../tmp/evil` resolves to a path outside `PLANS_DIR`.
- `buffet-bot plans --delete "../../../important-file"` → deletes `~/../important-file.json`
- `buffet-bot guide` → "Plan name: `../../.ssh/config`" → reads/overwrites `~/.ssh/config.json`

### Remediation

Add a path containment check in `_save_plan`, `_load_plan`, and the `plans --delete` inline block:

```python
import pathlib

def _safe_plan_path(name: str) -> pathlib.Path:
    """Resolve the plan file path and verify it stays inside PLANS_DIR."""
    plans_dir = pathlib.Path(PLANS_DIR).resolve()
    candidate = (plans_dir / f"{name}.json").resolve()
    if not str(candidate).startswith(str(plans_dir) + os.sep) and candidate != plans_dir:
        raise ValueError(f"Invalid plan name: {name!r}")
    return candidate
```

Then replace `os.path.join(PLANS_DIR, f"{name}.json")` with `_safe_plan_path(name)` in all three places, catching `ValueError` and showing a user-facing error message.

Additionally, restrict plan name characters at input time:
```python
import re
# Allow only alphanumeric, dash, underscore, dot — no slashes
if not re.match(r'^[\w.\-]{1,64}$', name):
    console.print("[red]Invalid plan name. Use only letters, numbers, dashes, and underscores.[/red]")
    return
```

### Fix Applied (session 4 — Security Auditor)

`import pathlib` added to `main.py` imports. `_safe_plan_path(name)` helper added near the `PLANS_DIR` constant. The function resolves both `PLANS_DIR` and the target path with `.resolve()`, then confirms the target starts with the plans directory prefix:

```python
def _safe_plan_path(name: str) -> pathlib.Path:
    plans_dir = pathlib.Path(PLANS_DIR).resolve()
    target = (plans_dir / f"{name}.json").resolve()
    if not str(target).startswith(str(plans_dir) + os.sep):
        raise ValueError(f"Invalid plan name: {name!r}")
    return target
```

Applied to all four user-controlled call sites (verified by grep):
1. `_save_plan()` — replaces `os.path.join`; caller wraps in `try/except ValueError`
2. `_load_plan()` — replaces `os.path.join`; returns `pathlib.Path`
3. `plans --delete` block — replaces `os.path.join`; catches `ValueError` before filesystem access
4. `guide --plan <name>` block (line ~2250) — catches `ValueError` before calling `_load_plan`
5. `plans --run <name>` block — catches `ValueError` before calling `_load_plan`

`_guide_load_plan()` (interactive menu) reads plan names from `_list_plans()` → `os.listdir(PLANS_DIR)` — file names from disk, not user input. No change needed there.

---

## FINDING-002: XML Entity Expansion in insiders.py

- **Severity:** P3 (low / informational)
- **Category:** Input Validation
- **File:** `buffet_bot/insiders.py` line 91
- **Status:** [ ] Open

### Description

`_parse_form4()` uses `xml.etree.ElementTree.fromstring()` to parse Form 4 XML downloaded from SEC EDGAR:

```python
root = ET.fromstring(r.content)
```

Python's `xml.etree.ElementTree` does **not** expand external entities by default (no XXE vulnerability). However, it also does not limit internal entity expansion depth, making it theoretically vulnerable to a "billion laughs" DoS attack if the data source returned a crafted XML payload with deeply nested entity references.

**Practical risk is very low** because:
- The data source is `sec.gov` — a US government domain over HTTPS
- The URL is constructed from a CIK (integer) and accession number (alphanumeric), both validated to be numeric/clean before use in the URL
- An attacker would need to compromise SEC EDGAR to exploit this

### Remediation

Switch to `defusedxml` for defense in depth:

```python
# requirements.txt — add:
defusedxml>=0.7.1

# insiders.py — replace:
import xml.etree.ElementTree as ET
# with:
import defusedxml.ElementTree as ET
```

`defusedxml` is a drop-in replacement that also blocks entity expansion attacks. The rest of the code is unchanged.

### Fix Owner

ENG (one-line dependency + one-line import change).

---

## Security Tests

`tests/test_security.py` created 2026-03-01 (Security Auditor, session 4).
Covers: path traversal (6 cases), no-cloud-LLM imports (3 checks), SQL injection (2 cases), credential leakage (2 cases).

---

## Recommendations for v1.0.0

The v1.0.0 roadmap lists additional security items not yet scheduled:

1. **Dependency CVE scan** (`pip-audit -r requirements.txt`) — run before PyPI publish
2. **`SECURITY.md` vulnerability disclosure policy** — standard open-source practice
3. **FINDING-002 defusedxml** — swap `xml.etree.ElementTree` for `defusedxml` in `insiders.py` (ENG, one-line fix)

---

## Appendix: Outbound Network Endpoints Observed

| Module | Endpoint | Purpose |
|--------|----------|---------|
| `main.py` | `https://api.stlouisfed.org/fred/series/observations` | FRED macro data |
| `main.py` | `https://api.nasdaq.com/api/calendar/earnings` | Earnings calendar |
| `main.py` | `https://data.alpaca.markets/v1beta1/news` | Alpaca news headlines |
| `main.py` | `https://paper-api.alpaca.markets/v2/account/portfolio/history` | Portfolio history |
| `insiders.py` | `https://www.sec.gov/files/company_tickers.json` | CIK lookup |
| `insiders.py` | `https://data.sec.gov/submissions/CIK{cik}.json` | Filing index |
| `insiders.py` | `https://www.sec.gov/Archives/edgar/data/…` | Form 4 XML |
| `universe.py` | `https://www.sec.gov/files/company_tickers.json` | EDGAR live search |
| `politicians.py` | `https://house-stock-watcher-data.s3-us-east-2.amazonaws.com/…` | House Stock Watcher |
| `politicians.py` | `https://financialmodelingprep.com/api/v4/…` | FMP congressional trades |
| `crypto.py` | Alpaca crypto data API | Crypto bars/quotes |
| `crypto.py` | Coinbase Advanced Trade API | Crypto orders |

All endpoints use HTTPS. All are within the approved domain list documented in `agents/security-auditor.md`.

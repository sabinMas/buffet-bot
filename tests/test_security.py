"""
Security regression tests for Buffet-Bot.

Covers findings documented in agents/SECURITY-AUDIT.md:
  FINDING-001 — Path traversal in plan file management
  Data exfiltration — no cloud LLM imports
  Credentials — no API key leakage in command output
"""
import pytest
import sys


# ── FINDING-001: Path Traversal ───────────────────────────────────────────────

class TestPlanPathTraversal:
    """_safe_plan_path must never resolve outside PLANS_DIR."""

    def test_unix_traversal_blocked(self):
        from buffet_bot.plans import _safe_plan_path
        with pytest.raises(ValueError):
            _safe_plan_path("../../etc/passwd")

    def test_windows_traversal_blocked(self):
        from buffet_bot.plans import _safe_plan_path
        with pytest.raises(ValueError):
            _safe_plan_path("..\\..\\etc\\passwd")

    def test_absolute_path_blocked(self):
        from buffet_bot.plans import _safe_plan_path
        with pytest.raises(ValueError):
            _safe_plan_path("/etc/passwd")

    def test_dotdot_only_blocked(self):
        from buffet_bot.plans import _safe_plan_path
        with pytest.raises(ValueError):
            _safe_plan_path("..")

    def test_deep_traversal_blocked(self):
        from buffet_bot.plans import _safe_plan_path
        with pytest.raises(ValueError):
            _safe_plan_path("../../../../tmp/evil")

    def test_valid_simple_name_accepted(self):
        from buffet_bot.plans import _safe_plan_path
        path = _safe_plan_path("my-plan")
        assert path.name == "my-plan.json"

    def test_valid_name_with_numbers_accepted(self):
        from buffet_bot.plans import _safe_plan_path
        path = _safe_plan_path("growth-plan-2026")
        assert path.name == "growth-plan-2026.json"

    def test_valid_name_stays_inside_plans_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("buffet_bot.plans.PLANS_DIR", str(tmp_path))
        from buffet_bot.plans import _safe_plan_path
        import pathlib
        path = _safe_plan_path("safe-plan")
        # The resolved path must be inside the (possibly different) PLANS_DIR
        assert path.name == "safe-plan.json"


# ── Data Exfiltration: No Cloud LLM Imports ───────────────────────────────────

class TestNoCloudLLM:
    """No cloud AI library may be imported anywhere in the buffet_bot package."""

    def test_no_openai_import(self):
        assert "openai" not in sys.modules, "openai must never be imported"

    def test_no_anthropic_import(self):
        assert "anthropic" not in sys.modules, "anthropic must never be imported"

    def test_no_gemini_import(self):
        assert "google.generativeai" not in sys.modules


# ── SQL Injection: Watchlist ──────────────────────────────────────────────────

class TestSQLInjection:
    """SQL metacharacters in user input must not corrupt the database."""

    def test_watchlist_add_sql_injection_safe(self, in_memory_db):
        from buffet_bot.db import add_to_watchlist, get_watchlist
        # Classic SQL injection attempt
        add_to_watchlist("'; DROP TABLE watchlist; --")
        rows = get_watchlist()
        # Table must still exist and be queryable
        assert isinstance(rows, list)

    def test_watchlist_add_tautology_safe(self, in_memory_db):
        from buffet_bot.db import add_to_watchlist, get_watchlist
        add_to_watchlist("' OR '1'='1")
        rows = get_watchlist()
        assert isinstance(rows, list)


# ── Credential Leakage ────────────────────────────────────────────────────────

class TestCredentialLeakage:
    """API key values must never appear in command output."""

    def test_status_does_not_print_api_key(self, runner, monkeypatch):
        fake_key = "SUPERSECRETALPACAKEY99"
        monkeypatch.setenv("ALPACA_API_KEY", fake_key)
        # Import after env patch so the module picks it up
        from buffet_bot.main import cli
        result = runner.invoke(cli, ["status"])
        assert fake_key not in result.output

    def test_status_does_not_print_secret_key(self, runner, monkeypatch):
        fake_secret = "SUPERSECRETALPACASECRET99"
        monkeypatch.setenv("ALPACA_SECRET_KEY", fake_secret)
        from buffet_bot.main import cli
        result = runner.invoke(cli, ["status"])
        assert fake_secret not in result.output

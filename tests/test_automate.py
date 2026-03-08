"""Tests for the automate command, agent loop, and crumb-error resilience."""
import json
import pytest
from unittest.mock import MagicMock, patch, call

from buffet_bot.main import cli
from buffet_bot.automate import run_agent_loop, _extract_json


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _agent_json(tool, args=None, thought="ok"):
    """Return a JSON string the LLM would produce for a single tool call."""
    return json.dumps({"thought": thought, "tool": tool, "args": args or {}})


def _done_json(summary="all done"):
    return _agent_json("done", {"summary": summary})


def _make_ollama(responses: list):
    """Return a mock ollama module whose .chat() returns responses in sequence."""
    mock = MagicMock()
    mock.chat.side_effect = [
        {"message": {"content": r}} for r in responses
    ]
    return mock


def _make_console():
    """Return a MagicMock console where .status() is a usable context manager."""
    console = MagicMock()
    console.status.return_value.__enter__ = MagicMock(return_value=None)
    console.status.return_value.__exit__  = MagicMock(return_value=False)
    return console


def _minimal_tools(extra=None):
    """Minimal tools dict with a stub 'scan' tool plus 'done' sentinel."""
    stub_fn = MagicMock(return_value={"ok": True})
    tools = {
        "scan": {
            "description": "scan stocks",
            "params":      "top=5",
            "fn":          stub_fn,
        },
    }
    if extra:
        tools.update(extra)
    return tools, stub_fn


# ── TestExtractJson ───────────────────────────────────────────────────────────

class TestExtractJson:
    """Pure-function tests for _extract_json — no mocking needed."""

    def test_clean_json_parses(self):
        result = _extract_json('{"tool": "done", "args": {}}')
        assert result == {"tool": "done", "args": {}}

    def test_strips_think_block(self):
        text = '<think>I should scan first</think>{"tool": "scan", "args": {"top": 5}}'
        result = _extract_json(text)
        assert result is not None
        assert result["tool"] == "scan"

    def test_strips_markdown_fence(self):
        text = '```json\n{"tool": "done", "args": {"summary": "ok"}}\n```'
        result = _extract_json(text)
        assert result is not None
        assert result["tool"] == "done"

    def test_extracts_embedded_json(self):
        text = 'Here is my response: {"tool": "scan", "args": {}} — done.'
        result = _extract_json(text)
        assert result is not None
        assert result["tool"] == "scan"

    def test_returns_none_on_garbage(self):
        assert _extract_json("This is just a sentence with no JSON.") is None

    def test_returns_none_on_empty_string(self):
        assert _extract_json("") is None


# ── TestRunAgentLoop ──────────────────────────────────────────────────────────

class TestRunAgentLoop:
    """Tests for run_agent_loop() called directly — LLM and tools fully mocked."""

    def _run(self, responses, tools=None, max_steps=10, execute=False, goal="test"):
        if tools is None:
            tools, _ = _minimal_tools()
        ollama = _make_ollama(responses)
        console = _make_console()
        return run_agent_loop(
            goal=goal,
            tools=tools,
            model="deepseek-r1",
            budget=500.0,
            max_steps=max_steps,
            execute=execute,
            console=console,
            ollama_module=ollama,
        ), ollama, console

    def test_done_on_first_step_returns_summary(self):
        result, _, _ = self._run([_done_json("portfolio complete")])
        assert result["summary"] == "portfolio complete"
        assert result["steps_taken"] == 1
        assert result["timed_out"] is False

    def test_result_dict_has_required_keys(self):
        result, _, _ = self._run([_done_json()])
        assert "summary"     in result
        assert "steps_taken" in result
        assert "timed_out"   in result

    def test_tool_fn_called_with_correct_args(self):
        tools, stub_fn = _minimal_tools()
        responses = [
            _agent_json("scan", {"top": 3}),
            _done_json(),
        ]
        self._run(responses, tools=tools)
        stub_fn.assert_called_once_with(top=3)

    def test_json_parse_failure_retries(self):
        """Garbage response is followed by valid done → steps_taken reflects both."""
        result, _, _ = self._run(["NOT JSON AT ALL", _done_json()])
        assert result["timed_out"] is False
        # parse failure counts as a step (hint injected + retry on next LLM call)
        assert result["steps_taken"] >= 1

    def test_unknown_tool_returns_error_to_llm(self):
        """Calling a tool not in the registry injects an error string into messages."""
        tools, _ = _minimal_tools()
        responses = [
            _agent_json("nonexistent_tool", {}),
            _done_json(),
        ]
        result, ollama_mock, _ = self._run(responses, tools=tools)
        # Second call to ollama.chat should include error context in messages
        second_call_messages = ollama_mock.chat.call_args_list[1][1]["messages"]
        last_user_msg = next(
            m["content"] for m in reversed(second_call_messages)
            if m["role"] == "user"
        )
        assert "error" in last_user_msg.lower() or "unknown" in last_user_msg.lower()

    def test_max_steps_timeout_sets_timed_out(self):
        """LLM never calls done → timed_out=True after max_steps."""
        tools, _ = _minimal_tools()
        # Keep returning scan — never done
        responses = [_agent_json("scan", {"top": 1})] * 3
        result, _, _ = self._run(responses, tools=tools, max_steps=2)
        assert result["timed_out"] is True

    def test_steps_taken_matches_iterations(self):
        """3-step sequence: scan, scan, done → steps_taken=3."""
        tools, _ = _minimal_tools()
        responses = [
            _agent_json("scan", {"top": 1}),
            _agent_json("scan", {"top": 2}),
            _done_json("finished"),
        ]
        result, _, _ = self._run(responses, tools=tools)
        assert result["steps_taken"] == 3

    def test_llm_exception_exits_gracefully(self):
        """Ollama raising an exception must not propagate — loop exits cleanly."""
        tools, _ = _minimal_tools()
        ollama = MagicMock()
        ollama.chat.side_effect = Exception("connection refused")
        console = _make_console()
        result = run_agent_loop(
            goal="test",
            tools=tools,
            model="deepseek-r1",
            budget=500.0,
            max_steps=5,
            execute=False,
            console=console,
            ollama_module=ollama,
        )
        assert "summary" in result
        assert "timed_out" in result

    def test_execute_false_directive_absent_from_system_prompt(self):
        """When execute=False, EXECUTE_DIRECTIVE text absent from system message."""
        tools, _ = _minimal_tools()
        ollama = _make_ollama([_done_json()])
        console = _make_console()
        run_agent_loop(
            goal="test",
            tools=tools,
            model="deepseek-r1",
            budget=500.0,
            max_steps=5,
            execute=False,
            console=console,
            ollama_module=ollama,
        )
        system_content = ollama.chat.call_args_list[0][1]["messages"][0]["content"]
        assert "You have permission to place paper trades" not in system_content

    def test_execute_true_directive_in_system_prompt(self):
        """When execute=True, execute directive IS present in system message."""
        tools, _ = _minimal_tools()
        ollama = _make_ollama([_done_json()])
        console = _make_console()
        run_agent_loop(
            goal="test",
            tools=tools,
            model="deepseek-r1",
            budget=500.0,
            max_steps=5,
            execute=True,
            console=console,
            ollama_module=ollama,
        )
        system_content = ollama.chat.call_args_list[0][1]["messages"][0]["content"]
        assert "permission to place paper trades" in system_content


# ── TestAutomateCrumbResilience ───────────────────────────────────────────────

class TestAutomateCrumbResilience:
    """Tests that yfinance crumb-style errors degrade gracefully in tool functions.

    Calls tool closures in isolation by constructing them via _build_automate_tools.
    No real yfinance, Alpaca, or LLM calls are made.
    """

    def _make_tools(self, **kwargs):
        from buffet_bot.cmd_account import _build_automate_tools
        return _build_automate_tools(
            execute=False,
            budget=500.0,
            primary_model="deepseek-r1",
            **kwargs,
        )

    def test_scan_stocks_partial_crumb_errors_returns_partial_results(self):
        """Half of get_buffett_metrics calls raise — remaining results are returned."""
        # 20 tickers in DEFAULT_SCAN_TICKERS — alternate error/success
        side_effects = []
        for i in range(20):
            if i % 2 == 0:
                side_effects.append(Exception("crumb error"))
            else:
                side_effects.append({"score": 50 + i})

        with patch('buffet_bot.cmd_account.trading_client'):
            with patch('buffet_bot.cmd_account.get_buffett_metrics',
                       side_effect=side_effects):
                tools = self._make_tools()
                result = tools["scan_stocks"]["fn"](top=5)

        # Should not crash; should return up to top successful results
        assert isinstance(result, list)
        assert len(result) <= 5

    def test_scan_stocks_all_crumb_errors_no_crash(self):
        """All get_buffett_metrics calls raise → scan falls back to score=0, no crash."""
        with patch('buffet_bot.cmd_account.trading_client'):
            with patch('buffet_bot.cmd_account.get_buffett_metrics',
                       side_effect=Exception("crumb")):
                tools = self._make_tools()
                result = tools["scan_stocks"]["fn"](top=5)

        # Closure catches each exception and substitutes {'score': 0}
        # so we still get top items — they just all have score=0
        assert isinstance(result, list)
        assert all(r["score"] == 0 for r in result)

    def test_analyze_stock_crumb_error_returns_error_dict(self):
        """_run_analysis raising → analyze_stock returns dict with 'error' key."""
        with patch('buffet_bot.cmd_account.trading_client'):
            with patch('buffet_bot.cmd_account._run_analysis',
                       side_effect=Exception("KeyError: 'crumb'")):
                tools = self._make_tools()
                result = tools["analyze_stock"]["fn"](ticker="AAPL")

        assert "error" in result
        assert "AAPL" in result["error"]
        # Should still return a safe consensus fallback
        assert result.get("consensus") == "HOLD"

    def test_scan_stocks_top_param_limits_and_sorts(self):
        """scan_stocks(top=3) with deterministic scores returns 3 items, sorted desc."""
        tickers = [
            'AAPL', 'MSFT', 'GOOGL', 'BRK-B', 'JNJ', 'V', 'JPM', 'PG',
            'KO', 'WMT', 'ABBV', 'MRK', 'LLY', 'TMO', 'UNH', 'HD',
            'AMZN', 'NVDA', 'META', 'TSLA',
        ]

        def _score_by_ticker(t):
            return {"score": tickers.index(t) * 5}

        with patch('buffet_bot.cmd_account.trading_client'):
            with patch('buffet_bot.cmd_account.get_buffett_metrics',
                       side_effect=_score_by_ticker):
                tools = self._make_tools()
                result = tools["scan_stocks"]["fn"](top=3)

        assert len(result) == 3
        scores = [r["score"] for r in result]
        assert scores == sorted(scores, reverse=True)


# ── TestAutomateCommand ───────────────────────────────────────────────────────

class TestAutomateCommand:
    """CLI-level tests for `buffet-bot automate` via CliRunner."""

    _LOOP_RESULT = {"summary": "done", "steps_taken": 1, "timed_out": False}

    def _invoke(self, runner, args, loop_result=None, ollama_up=True):
        if loop_result is None:
            loop_result = self._LOOP_RESULT
        with patch('buffet_bot.cmd_account.ensure_ollama_running', return_value=ollama_up):
            with patch('buffet_bot.cmd_account.run_agent_loop', return_value=loop_result) as mock_loop:
                with patch('buffet_bot.cmd_account.trading_client'):
                    result = runner.invoke(cli, args)
        return result, mock_loop

    def test_help_exits_zero(self, runner):
        result = runner.invoke(cli, ['automate', '--help'])
        assert result.exit_code == 0
        assert 'goal' in result.output.lower()

    def test_dry_run_never_calls_submit_order(self, runner):
        """Without --execute, Alpaca submit_order is never called."""
        mock_tc = MagicMock()
        with patch('buffet_bot.cmd_account.ensure_ollama_running', return_value=True):
            with patch('buffet_bot.cmd_account.run_agent_loop', return_value=self._LOOP_RESULT):
                with patch('buffet_bot.cmd_account.trading_client', mock_tc):
                    runner.invoke(cli, ['automate', 'invest $100'])
        mock_tc.submit_order.assert_not_called()

    def test_goal_arg_skips_wizard(self, runner):
        """Passing a goal argument bypasses the interactive wizard (Prompt.ask)."""
        with patch('buffet_bot.cmd_account.Prompt') as mock_prompt:
            result, _ = self._invoke(runner, ['automate', 'find top value stock'])
        mock_prompt.ask.assert_not_called()
        assert result.exit_code == 0

    def test_ollama_not_reachable_exits_zero(self, runner):
        """When Ollama is not running, command exits 0 and agent loop is not called."""
        result, mock_loop = self._invoke(
            runner, ['automate', 'invest $100'], ollama_up=False
        )
        assert result.exit_code == 0
        mock_loop.assert_not_called()

    def test_goal_propagated_to_agent_loop(self, runner):
        """Goal CLI argument reaches run_agent_loop's goal kwarg."""
        result, mock_loop = self._invoke(runner, ['automate', 'invest $200 wisely'])
        assert result.exit_code == 0
        assert mock_loop.call_count == 1
        assert mock_loop.call_args.kwargs["goal"] == "invest $200 wisely"

    def test_execute_flag_propagated(self, runner):
        """--execute sets execute=True when run_agent_loop is called."""
        result, mock_loop = self._invoke(
            runner, ['automate', 'buy something', '--execute']
        )
        assert result.exit_code == 0
        assert mock_loop.call_args.kwargs["execute"] is True

    def test_budget_flag_propagated(self, runner):
        """--budget 250 reaches run_agent_loop as budget=250.0."""
        result, mock_loop = self._invoke(
            runner, ['automate', 'buy something', '--budget', '250']
        )
        assert result.exit_code == 0
        assert abs(mock_loop.call_args.kwargs["budget"] - 250.0) < 0.01

    def test_timed_out_result_prints_warning(self, runner):
        """When run_agent_loop returns timed_out=True, output contains warning."""
        timed_result = {"summary": "", "steps_taken": 10, "timed_out": True}
        result, _ = self._invoke(
            runner, ['automate', 'invest $100'], loop_result=timed_result
        )
        assert result.exit_code == 0
        assert 'steps' in result.output.lower() or 'stopped' in result.output.lower()

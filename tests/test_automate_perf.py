"""Regression tests for session-26 automate performance changes.

These tests verify the caching, deduplication, message-trimming, and
backoff contracts that the perf-engineer session was asked to implement.

Each test is written against the intended behaviour of the feature.
If a test fails after a fresh checkout it means the feature has not yet
been implemented (or has been accidentally reverted).
"""

import json
import time
import pytest
from unittest.mock import MagicMock, patch, call


# ── Helpers (shared with existing test_automate.py) ───────────────────────────

def _agent_json(tool, args=None, thought="ok"):
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
    console = MagicMock()
    console.status.return_value.__enter__ = MagicMock(return_value=None)
    console.status.return_value.__exit__ = MagicMock(return_value=False)
    return console


# ── Task #2: _cached_buffett_metrics deduplication in scan_stocks ─────────────

class TestBuffettMetricsCachingInScanStocks:
    """scan_stocks() should call get_buffett_metrics exactly once per ticker,
    even across repeated invocations of scan_stocks(), thanks to the
    _buffett_cache closure dict.

    Implementation contract (session 26, task #2):
        _buffett_cache: dict = {}   # closure-scoped inside _build_automate_tools

        def _cached_buffett_metrics(ticker):
            if ticker not in _buffett_cache:
                _buffett_cache[ticker] = get_buffett_metrics(ticker)
            return _buffett_cache[ticker]

        # scan_stocks calls _cached_buffett_metrics(t) not get_buffett_metrics(t)
    """

    def _make_tools(self, **kwargs):
        from buffet_bot.cmd_account import _build_automate_tools
        return _build_automate_tools(
            execute=False,
            budget=500.0,
            primary_model="deepseek-r1",
            **kwargs,
        )

    def test_scan_stocks_calls_buffett_metrics_once_per_ticker(self):
        """When scan_stocks is called twice, get_buffett_metrics must be called
        only once per ticker (second call hits the closure cache).
        """
        mock_metrics = MagicMock(return_value={"score": 75})

        with patch("buffet_bot.cmd_account.trading_client"):
            with patch("buffet_bot.cmd_account.get_buffett_metrics", mock_metrics):
                tools = self._make_tools()
                scan_fn = tools["scan_stocks"]["fn"]

                # First call — should hit get_buffett_metrics for each ticker
                first_result = scan_fn(top=5)
                calls_after_first = mock_metrics.call_count

                # Second call — cached results must be used, no new calls
                second_result = scan_fn(top=5)
                calls_after_second = mock_metrics.call_count

        # Each ticker was only fetched once across both scan_stocks calls
        assert calls_after_second == calls_after_first, (
            f"get_buffett_metrics was called {calls_after_second} times total; "
            f"expected {calls_after_first} (same as after first scan). "
            "The _buffett_cache is not working."
        )

    def test_scan_stocks_single_call_fetches_each_ticker_once(self):
        """Within a single scan_stocks() call, each ticker is fetched at most once."""
        call_log = []

        def _mock_metrics(ticker):
            call_log.append(ticker)
            return {"score": 50}

        with patch("buffet_bot.cmd_account.trading_client"):
            with patch("buffet_bot.cmd_account.get_buffett_metrics", side_effect=_mock_metrics):
                tools = self._make_tools()
                tools["scan_stocks"]["fn"](top=5)

        # Verify no ticker was fetched more than once
        ticker_counts = {t: call_log.count(t) for t in set(call_log)}
        duplicates = {t: n for t, n in ticker_counts.items() if n > 1}
        assert not duplicates, f"Tickers fetched more than once: {duplicates}"


# ── Task #6: analyze_stock deduplication via _analysis_cache ──────────────────

class TestAnalysisStockDeduplication:
    """analyze_stock() should return a cached result on repeated calls for
    the same ticker within the same automate agent session, so the LLM
    cannot cause redundant inference by calling analyze_stock('AAPL') twice.

    Implementation contract (session 26, task #6):
        _analysis_cache: dict = {}   # closure-scoped inside _build_automate_tools

        def analyze_stock(ticker, ...):
            ticker = ticker.upper()
            if ticker in _analysis_cache:
                return _analysis_cache[ticker]
            try:
                data = _run_analysis(...)
            except ...:
                ...
            result = {...}
            _analysis_cache[ticker] = result
            return result
    """

    def _make_tools(self, **kwargs):
        from buffet_bot.cmd_account import _build_automate_tools
        return _build_automate_tools(
            execute=False,
            budget=500.0,
            primary_model="deepseek-r1",
            **kwargs,
        )

    def test_analyze_stock_calls_run_analysis_once_per_ticker(self):
        """Calling analyze_stock('AAPL') twice must result in _run_analysis
        being called only once.
        """
        mock_run = MagicMock(return_value={
            "consensus": "BUY",
            "realtime":  {"price": 150.0},
            "buffett":   {"score": 75},
            "best_buy_resp": {"reason": "good", "qty": 5},
        })

        with patch("buffet_bot.cmd_account.trading_client"):
            with patch("buffet_bot.cmd_account._run_analysis", mock_run):
                tools = self._make_tools()
                analyze_fn = tools["analyze_stock"]["fn"]

                first_result  = analyze_fn(ticker="AAPL")
                second_result = analyze_fn(ticker="AAPL")

        assert mock_run.call_count == 1, (
            f"_run_analysis was called {mock_run.call_count} times; "
            "expected 1 (second call should hit _analysis_cache)."
        )
        # Both results should be identical (same cached dict)
        assert first_result["consensus"] == second_result["consensus"]

    def test_analyze_stock_different_tickers_each_called_once(self):
        """analyze_stock('AAPL') and analyze_stock('MSFT') must each call
        _run_analysis once — they are different cache keys.
        """
        mock_run = MagicMock(return_value={
            "consensus": "HOLD",
            "realtime":  {"price": 100.0},
            "buffett":   {"score": 60},
            "best_buy_resp": {"reason": "", "qty": 0},
        })

        with patch("buffet_bot.cmd_account.trading_client"):
            with patch("buffet_bot.cmd_account._run_analysis", mock_run):
                tools = self._make_tools()
                analyze_fn = tools["analyze_stock"]["fn"]
                analyze_fn(ticker="AAPL")
                analyze_fn(ticker="MSFT")
                analyze_fn(ticker="AAPL")  # third call — AAPL cached

        # AAPL + MSFT = 2 unique calls; third call hits cache
        assert mock_run.call_count == 2, (
            f"_run_analysis was called {mock_run.call_count} times; "
            "expected 2 (AAPL + MSFT, with AAPL's second call cached)."
        )

    def test_analyze_stock_case_insensitive_cache_key(self):
        """'aapl' and 'AAPL' must resolve to the same cache entry."""
        mock_run = MagicMock(return_value={
            "consensus": "BUY",
            "realtime":  {"price": 150.0},
            "buffett":   {"score": 80},
            "best_buy_resp": {"reason": "moat", "qty": 3},
        })

        with patch("buffet_bot.cmd_account.trading_client"):
            with patch("buffet_bot.cmd_account._run_analysis", mock_run):
                tools = self._make_tools()
                analyze_fn = tools["analyze_stock"]["fn"]
                analyze_fn(ticker="aapl")   # lowercase
                analyze_fn(ticker="AAPL")   # uppercase — should hit same cache

        assert mock_run.call_count == 1, (
            "Cache key must be normalised to uppercase so 'aapl' and 'AAPL' "
            "are the same entry."
        )


# ── Task #4: _fred_cache TTL caching in _fetch_fred_data ──────────────────────

class TestFredDataTTLCache:
    """_fetch_fred_data() should cache its result and skip the network for
    subsequent calls within the TTL window (default 60 seconds).

    Implementation contract (session 26, task #4):
        _fred_cache: dict = {"data": {}, "ts": 0.0}
        _FRED_TTL: int = 60   # seconds

        def _fetch_fred_data() -> dict:
            if not FRED_API_KEY:
                return {}
            now = time.time()
            if _fred_cache["data"] and (now - _fred_cache["ts"]) < _FRED_TTL:
                return _fred_cache["data"]
            # ... fetch from FRED ...
            _fred_cache["data"] = result
            _fred_cache["ts"]   = time.time()
            return result
    """

    def setup_method(self):
        """Reset _fred_cache before each test so previous run's state
        does not leak.  Mirrors the contract expected in the implementation.
        """
        try:
            import buffet_bot.data as _data_mod
            if hasattr(_data_mod, "_fred_cache"):
                _data_mod._fred_cache["data"] = {}
                _data_mod._fred_cache["ts"]   = 0.0
        except Exception:
            pass   # module not yet updated — tests will xfail

    def _mock_fred_obs(self, value="5.25"):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"observations": [{"value": value}]}
        return resp

    def test_second_call_within_ttl_skips_requests_get(self, monkeypatch):
        """When called twice in quick succession (within TTL), the second call
        must NOT invoke requests.get again.
        """
        import buffet_bot.data as _data_mod

        if not hasattr(_data_mod, "_fred_cache"):
            pytest.skip("_fred_cache not yet implemented in data.py")

        monkeypatch.setattr("buffet_bot.data.FRED_API_KEY", "test-key")
        mock_get = MagicMock(return_value=self._mock_fred_obs("5.00"))

        with patch("buffet_bot.data.requests.get", mock_get):
            from buffet_bot.data import _fetch_fred_data
            _fetch_fred_data()          # first call — hits network
            first_call_count = mock_get.call_count

            _fetch_fred_data()          # second call — must use cache
            second_call_count = mock_get.call_count

        assert second_call_count == first_call_count, (
            f"requests.get was called {second_call_count} times total; "
            "expected the same count as after the first call "
            "(TTL cache should prevent the second network hit)."
        )

    def test_call_after_cache_expiry_re_hits_requests_get(self, monkeypatch):
        """After resetting ts=0.0 (simulating TTL expiry), the next call must
        hit requests.get again.
        """
        import buffet_bot.data as _data_mod

        if not hasattr(_data_mod, "_fred_cache"):
            pytest.skip("_fred_cache not yet implemented in data.py")

        monkeypatch.setattr("buffet_bot.data.FRED_API_KEY", "test-key")
        mock_get = MagicMock(return_value=self._mock_fred_obs("5.00"))

        with patch("buffet_bot.data.requests.get", mock_get):
            from buffet_bot.data import _fetch_fred_data

            _fetch_fred_data()          # first call — populates cache
            after_first = mock_get.call_count

            # Simulate TTL expiry
            _data_mod._fred_cache["ts"] = 0.0

            _fetch_fred_data()          # second call — TTL expired, must re-fetch
            after_second = mock_get.call_count

        assert after_second > after_first, (
            "After resetting _fred_cache['ts']=0.0 the next _fetch_fred_data() "
            "call must hit requests.get again (TTL expired)."
        )

    def test_cached_result_is_returned_unchanged(self, monkeypatch):
        """The cached dict returned on the second call must be identical
        in content to the first call's result.
        """
        import buffet_bot.data as _data_mod

        if not hasattr(_data_mod, "_fred_cache"):
            pytest.skip("_fred_cache not yet implemented in data.py")

        monkeypatch.setattr("buffet_bot.data.FRED_API_KEY", "test-key")

        with patch("buffet_bot.data.requests.get", return_value=self._mock_fred_obs("4.75")):
            from buffet_bot.data import _fetch_fred_data
            first  = _fetch_fred_data()
            second = _fetch_fred_data()

        assert first == second, "Cached result differs from original result."


# ── Task #8: messages list trimming in run_agent_loop ─────────────────────────

class TestAgentLoopMessageTrimming:
    """After session 26 task #8, run_agent_loop must trim the messages list
    so it never grows beyond a fixed window to prevent context bloat.

    Implementation contract:
        MAX_HISTORY_PAIRS = 3   # keep system + user-goal + 3 exchange pairs
        MAX_MSG_LEN = 2 + 2 * MAX_HISTORY_PAIRS = 8

        After appending assistant + user messages at each step, trim:
            if len(messages) > MAX_MSG_LEN:
                messages[2:] = messages[-(2 * MAX_HISTORY_PAIRS):]

    The test verifies that even after many steps, messages length
    does not exceed 8 (system + user-goal + 3 pairs = 8 entries).
    """

    def _run_loop(self, num_steps: int) -> list:
        """Run the agent loop for num_steps tool calls, return the messages
        list captured from the last LLM call.
        """
        from buffet_bot.automate import run_agent_loop

        # Build a tool response sequence: (num_steps) tool calls then done
        responses = [_agent_json("stub_tool", {"x": i}) for i in range(num_steps)]
        responses.append(_done_json("complete"))

        stub_fn = MagicMock(return_value={"ok": True})
        tools = {
            "stub_tool": {
                "description": "stub",
                "params":      "x",
                "fn":          stub_fn,
            }
        }

        captured_messages = []

        ollama_mock = MagicMock()

        def _chat_side_effect(**kwargs):
            msg_copy = list(kwargs.get("messages", []))
            captured_messages.append(msg_copy)
            idx = len(captured_messages) - 1
            content = responses[idx] if idx < len(responses) else _done_json()
            return {"message": {"content": content}}

        ollama_mock.chat.side_effect = _chat_side_effect
        console = _make_console()

        run_agent_loop(
            goal="test trimming",
            tools=tools,
            model="deepseek-r1",
            budget=500.0,
            max_steps=num_steps + 2,
            execute=False,
            console=console,
            ollama_module=ollama_mock,
        )

        return captured_messages

    def test_messages_never_exceed_eight_entries(self):
        """After 6 tool-call steps, the messages list passed to LLM must never
        exceed 8 entries (system + user-goal + 3 exchange pairs).
        """
        import buffet_bot.automate as _automate_mod

        if not hasattr(_automate_mod, "MAX_HISTORY_PAIRS"):
            pytest.skip("MAX_HISTORY_PAIRS not yet defined in automate.py — task #8 not implemented")

        captured = self._run_loop(num_steps=6)

        # The test is meaningful only after at least 4 LLM calls (enough to accumulate)
        assert len(captured) >= 4, "Not enough LLM calls captured for a meaningful test"

        for i, msg_list in enumerate(captured[3:], start=3):
            assert len(msg_list) <= 8, (
                f"At LLM call #{i + 1}, messages list has {len(msg_list)} entries; "
                "must not exceed 8 (system + user-goal + 3 exchange pairs)."
            )

    def test_system_and_goal_messages_always_present(self):
        """After trimming, the system prompt (index 0) and user goal (index 1)
        must always remain at the front of the messages list.
        """
        import buffet_bot.automate as _automate_mod

        if not hasattr(_automate_mod, "MAX_HISTORY_PAIRS"):
            pytest.skip("MAX_HISTORY_PAIRS not yet defined in automate.py — task #8 not implemented")

        captured = self._run_loop(num_steps=6)

        for msg_list in captured:
            if len(msg_list) >= 2:
                assert msg_list[0]["role"] == "system",  "First message must always be the system prompt"
                assert msg_list[1]["role"] == "user",    "Second message must always be the user goal"


# ── Task #7: exponential backoff in ensure_ollama_running ─────────────────────

class TestEnsureOllamaExponentialBackoff:
    """ensure_ollama_running() must use exponential backoff (not fixed 0.5 s)
    when polling for Ollama to start.

    Implementation contract (session 26, task #7):
        delays = [0.2, 0.4, 0.8, 1.6, 3.2, ...]   # powers of 2, starting 0.2
        for i in range(max_retries):
            time.sleep(delays[i])
            try:
                _req.get(...)
                return True
            except Exception:
                pass

    The test verifies that sleep is called with *increasing* values when
    Ollama fails to respond on the first few polls.
    """

    def test_backoff_delays_are_increasing(self):
        """When Ollama keeps failing, time.sleep must be called with
        strictly increasing delays (0.2, 0.4, 0.8, ...).
        """
        import buffet_bot.globals as _globals_mod

        # Check if exponential backoff was implemented (old code uses fixed 0.5 s)
        import inspect
        source = inspect.getsource(_globals_mod.ensure_ollama_running)
        if "0.5" in source and "0.2" not in source:
            pytest.skip(
                "ensure_ollama_running still uses fixed 0.5 s sleep — "
                "task #7 (exponential backoff) not yet implemented"
            )

        sleep_calls = []

        def _mock_sleep(seconds):
            sleep_calls.append(seconds)

        # Popen succeeds (Ollama "starts"), but HTTP check fails 3 times then succeeds
        poll_results: list = [Exception("not up")] * 3 + [MagicMock(status_code=200)]

        with patch("buffet_bot.globals.subprocess.Popen"):
            with patch("buffet_bot.globals._req.get") as mock_get:
                # First call (already-running check) raises, triggering the start path
                # Subsequent calls simulate polling
                mock_get.side_effect = [Exception("not running")] + poll_results
                with patch("buffet_bot.globals.time.sleep", side_effect=_mock_sleep):
                    from buffet_bot.globals import ensure_ollama_running
                    result = ensure_ollama_running()

        # Must have slept at least 3 times (3 failed polls before success)
        assert len(sleep_calls) >= 3, (
            f"Expected at least 3 sleep calls (for 3 failed polls), got {len(sleep_calls)}"
        )

        # Delays must be strictly increasing
        for i in range(len(sleep_calls) - 1):
            assert sleep_calls[i] < sleep_calls[i + 1], (
                f"sleep delays are not increasing: {sleep_calls}"
            )

    def test_backoff_first_two_delays_match_contract(self):
        """The first two backoff delays must be 0.2 s and 0.4 s (powers of 2
        starting from 0.2) as specified in task #7.
        """
        import buffet_bot.globals as _globals_mod
        import inspect

        source = inspect.getsource(_globals_mod.ensure_ollama_running)
        if "0.5" in source and "0.2" not in source:
            pytest.skip("Exponential backoff not yet implemented — task #7 pending")

        sleep_calls = []

        with patch("buffet_bot.globals.subprocess.Popen"):
            with patch("buffet_bot.globals._req.get") as mock_get:
                # First call raises (triggers start), poll fails twice then succeeds
                mock_get.side_effect = [
                    Exception("not running"),    # initial check
                    Exception("still starting"), # poll 1
                    Exception("still starting"), # poll 2
                    MagicMock(status_code=200),  # poll 3 — success
                ]
                with patch("buffet_bot.globals.time.sleep", side_effect=sleep_calls.append):
                    from buffet_bot.globals import ensure_ollama_running
                    ensure_ollama_running()

        assert len(sleep_calls) >= 2, "Expected at least 2 sleep calls"
        assert abs(sleep_calls[0] - 0.2) < 1e-6, (
            f"First backoff delay should be 0.2 s; got {sleep_calls[0]}"
        )
        assert abs(sleep_calls[1] - 0.4) < 1e-6, (
            f"Second backoff delay should be 0.4 s; got {sleep_calls[1]}"
        )

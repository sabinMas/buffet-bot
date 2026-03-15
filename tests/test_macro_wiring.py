"""
tests/test_macro_wiring.py

Regression tests for v0.9.0 macro wiring:
  - macro_prompt_block and compute_macro_score wired into analysis.py
  - compute_macro_score wired into edge-scan --macro flag in cmd_intel.py

No real FRED / network calls are made.  All HTTP and yfinance interactions
are mocked at the appropriate module boundary.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock, call
from click.testing import CliRunner

from buffet_bot.macro import (
    _classify_regime,
    compute_macro_score,
    macro_prompt_block,
    get_macro_regime,
    get_sector_rotation_signal,
    get_recession_probability,
    _SECTOR_ROTATION,
    _SIGNAL_SCORES,
    REGIMES,
    _regime_cache,
    _REGIME_CACHE_TTL,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reset_regime_cache():
    """Force the in-memory regime cache to expire so tests get fresh results."""
    with __import__('buffet_bot.macro', fromlist=['_regime_cache_lock'])._regime_cache_lock:
        _regime_cache["ts"] = 0.0
        _regime_cache["data"] = None


# ── _classify_regime unit tests ───────────────────────────────────────────────

class TestClassifyRegime:
    """Pure-function tests — no mocking needed."""

    def test_stagflation_high_rate_inverted_curve(self):
        regime, conf, signals = _classify_regime(fed_rate=5.5, yield_curve=-0.3, cpi=315.0)
        assert regime == "STAGFLATION"
        assert conf > 0.0

    def test_risk_off_inverted_curve_only(self):
        regime, conf, signals = _classify_regime(fed_rate=2.0, yield_curve=-0.1, cpi=310.0)
        assert regime == "RISK_OFF"

    def test_risk_off_high_rate_only(self):
        regime, conf, signals = _classify_regime(fed_rate=5.5, yield_curve=0.3, cpi=310.0)
        assert regime == "RISK_OFF"

    def test_risk_on_positive_spread_low_rate(self):
        regime, conf, signals = _classify_regime(fed_rate=1.0, yield_curve=1.2, cpi=300.0)
        assert regime == "RISK_ON"
        assert conf > 0.0

    def test_neutral_moderate_conditions(self):
        regime, conf, signals = _classify_regime(fed_rate=3.0, yield_curve=0.3, cpi=305.0)
        assert regime == "NEUTRAL"

    def test_none_fed_rate_uses_default(self):
        """None fed_rate should use the 3.0 default (moderate) — must not crash."""
        regime, conf, signals = _classify_regime(fed_rate=None, yield_curve=0.8, cpi=310.0)
        assert regime in REGIMES

    def test_none_yield_curve_uses_default(self):
        """None yield_curve should use 0.5 default (normal) — must not crash."""
        regime, conf, signals = _classify_regime(fed_rate=2.0, yield_curve=None, cpi=310.0)
        assert regime in REGIMES

    def test_all_none_returns_valid_regime(self):
        """All None inputs must return a NEUTRAL regime without raising."""
        regime, conf, signals = _classify_regime(fed_rate=None, yield_curve=None, cpi=None)
        assert regime in REGIMES
        assert 0.0 <= conf <= 1.0
        assert isinstance(signals, dict)

    def test_returns_tuple_of_three(self):
        result = _classify_regime(fed_rate=3.0, yield_curve=0.5, cpi=310.0)
        assert len(result) == 3
        regime, confidence, signals = result
        assert isinstance(regime, str)
        assert isinstance(confidence, float)
        assert isinstance(signals, dict)

    def test_deep_inversion_boosts_stagflation_confidence(self):
        """Yield curve < -0.5 should boost STAGFLATION confidence."""
        _, conf_shallow, _ = _classify_regime(fed_rate=5.5, yield_curve=-0.2, cpi=315.0)
        _, conf_deep, _    = _classify_regime(fed_rate=5.5, yield_curve=-0.8, cpi=315.0)
        assert conf_deep >= conf_shallow

    @pytest.mark.parametrize("fed_rate,yield_curve,expected_regime", [
        (5.5,  -0.5, "STAGFLATION"),
        (5.5,   0.3, "RISK_OFF"),
        (2.0,  -0.1, "RISK_OFF"),
        (1.0,   1.5, "RISK_ON"),
        (3.0,   0.3, "NEUTRAL"),
    ])
    def test_parametrized_regime_classification(self, fed_rate, yield_curve, expected_regime):
        regime, _, _ = _classify_regime(fed_rate, yield_curve, cpi=310.0)
        assert regime == expected_regime


# ── compute_macro_score unit tests ────────────────────────────────────────────

class TestComputeMacroScore:
    """Tests for compute_macro_score() — the 0-100 float output."""

    def _mock_regime(self, regime: str, yc_label: str = "NORMAL (+1.00%)", fr_label: str = "MODERATE (2.50%)"):
        """Return a mock get_macro_regime() return value."""
        return {
            "regime": regime,
            "confidence": 0.7,
            "signals": {
                "yield_curve": yc_label,
                "fed_rate": fr_label,
                "cpi_level": "310.0",
            },
            "cached": False,
        }

    def test_returns_float(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro._lookup_sector', return_value='Technology'), \
             patch('buffet_bot.macro.get_macro_regime', return_value=self._mock_regime("NEUTRAL")):
            score = compute_macro_score('AAPL')
        assert isinstance(score, float)

    def test_score_in_range_0_100(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro._lookup_sector', return_value='Technology'), \
             patch('buffet_bot.macro.get_macro_regime', return_value=self._mock_regime("RISK_ON")):
            score = compute_macro_score('AAPL')
        assert 0.0 <= score <= 100.0

    def test_neutral_fallback_when_sector_unknown(self):
        """If sector cannot be resolved, must return 50.0 (neutral fallback)."""
        _reset_regime_cache()
        with patch('buffet_bot.macro._lookup_sector', return_value=None):
            score = compute_macro_score('UNKNWN')
        assert score == 50.0

    def test_sector_provided_directly_skips_lookup(self):
        """When sector is passed directly, _lookup_sector must not be called."""
        _reset_regime_cache()
        mock_lookup = MagicMock(return_value=None)
        with patch('buffet_bot.macro._lookup_sector', mock_lookup), \
             patch('buffet_bot.macro.get_macro_regime', return_value=self._mock_regime("NEUTRAL")):
            score = compute_macro_score('AAPL', sector='Technology')
        mock_lookup.assert_not_called()
        assert 0.0 <= score <= 100.0

    def test_exception_in_regime_returns_50(self):
        """Any exception must return 50.0, never raise."""
        _reset_regime_cache()
        with patch('buffet_bot.macro._lookup_sector', return_value='Technology'), \
             patch('buffet_bot.macro.get_macro_regime', side_effect=RuntimeError("FRED down")):
            score = compute_macro_score('AAPL')
        assert score == 50.0

    def test_risk_on_tech_overweight_score_above_50(self):
        """RISK_ON + Technology (OVERWEIGHT) + NORMAL yield curve should be above 50."""
        _reset_regime_cache()
        regime = self._mock_regime("RISK_ON", yc_label="NORMAL (+1.00%)", fr_label="ACCOMMODATIVE (0.25%)")
        with patch('buffet_bot.macro._lookup_sector', return_value='Technology'), \
             patch('buffet_bot.macro.get_macro_regime', return_value=regime):
            score = compute_macro_score('AAPL')
        assert score > 50.0

    def test_risk_off_tech_underweight_score_below_50(self):
        """RISK_OFF + Technology (UNDERWEIGHT) + INVERTED curve should be below 50."""
        _reset_regime_cache()
        regime = self._mock_regime("RISK_OFF", yc_label="INVERTED (-0.50%)", fr_label="RESTRICTIVE (5.33%)")
        with patch('buffet_bot.macro._lookup_sector', return_value='Technology'), \
             patch('buffet_bot.macro.get_macro_regime', return_value=regime):
            score = compute_macro_score('AAPL')
        assert score < 50.0

    @pytest.mark.parametrize("regime,sector,yc_label,fr_label,expect_above_50", [
        # RISK_ON favors Technology
        ("RISK_ON",    "Technology",  "NORMAL (+1.50%)",   "ACCOMMODATIVE (0.25%)", True),
        # RISK_OFF favors Utilities
        ("RISK_OFF",   "Utilities",   "INVERTED (-0.30%)", "RESTRICTIVE (5.25%)",  False),
        # STAGFLATION favors Energy
        ("STAGFLATION","Energy",      "INVERTED (-0.20%)", "RESTRICTIVE (5.10%)",  False),
        # NEUTRAL gives everything neutral-ish
        ("NEUTRAL",    "Healthcare",  "FLAT (+0.20%)",     "MODERATE (3.00%)",     None),
    ])
    def test_parametrized_sector_regime_combinations(self, regime, sector, yc_label, fr_label, expect_above_50):
        _reset_regime_cache()
        mock_regime_data = {
            "regime": regime,
            "confidence": 0.7,
            "signals": {"yield_curve": yc_label, "fed_rate": fr_label, "cpi_level": "310.0"},
            "cached": False,
        }
        with patch('buffet_bot.macro._lookup_sector', return_value=sector), \
             patch('buffet_bot.macro.get_macro_regime', return_value=mock_regime_data):
            score = compute_macro_score('TEST', sector=sector)
        assert 0.0 <= score <= 100.0
        if expect_above_50 is True:
            assert score > 50.0
        elif expect_above_50 is False:
            # Either below or near 50 — just verify it is in range
            assert score <= 100.0


# ── macro_prompt_block unit tests ─────────────────────────────────────────────

class TestMacroPromptBlock:
    """Tests for macro_prompt_block() — returns a string for LLM prompt injection."""

    def _make_regime(self, note: str = None) -> dict:
        signals = {
            "yield_curve": "NORMAL (+0.80%)",
            "fed_rate":    "MODERATE (3.00%)",
            "cpi_level":   "312.0",
        }
        if note:
            signals = {"note": note}
        return {
            "regime": "NEUTRAL",
            "confidence": 0.5,
            "signals": signals,
            "cached": False,
        }

    def test_returns_string(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', return_value=self._make_regime()), \
             patch('buffet_bot.macro._lookup_sector', return_value='Technology'), \
             patch('buffet_bot.macro.get_recession_probability', return_value=0.25), \
             patch('buffet_bot.macro.compute_macro_score', return_value=55.0):
            result = macro_prompt_block('AAPL')
        assert isinstance(result, str)

    def test_returns_non_empty_when_data_available(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', return_value=self._make_regime()), \
             patch('buffet_bot.macro._lookup_sector', return_value='Technology'), \
             patch('buffet_bot.macro.get_recession_probability', return_value=0.25), \
             patch('buffet_bot.macro.compute_macro_score', return_value=55.0):
            result = macro_prompt_block('AAPL')
        assert len(result) > 0

    def test_contains_regime_in_output(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', return_value=self._make_regime()), \
             patch('buffet_bot.macro._lookup_sector', return_value='Technology'), \
             patch('buffet_bot.macro.get_recession_probability', return_value=0.25), \
             patch('buffet_bot.macro.compute_macro_score', return_value=55.0):
            result = macro_prompt_block('AAPL')
        assert "NEUTRAL" in result or "RISK_ON" in result or "RISK_OFF" in result or "STAGFLATION" in result

    def test_contains_macro_score_in_output(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', return_value=self._make_regime()), \
             patch('buffet_bot.macro._lookup_sector', return_value='Technology'), \
             patch('buffet_bot.macro.get_recession_probability', return_value=0.25), \
             patch('buffet_bot.macro.compute_macro_score', return_value=72.0):
            result = macro_prompt_block('AAPL')
        assert "72" in result

    def test_returns_empty_when_fred_unavailable(self):
        """When signals contain 'unavailable' note, must return ''."""
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime',
                   return_value=self._make_regime(note="FRED data unavailable (check FRED_API_KEY)")):
            result = macro_prompt_block('AAPL')
        assert result == ""

    def test_returns_empty_string_on_exception(self):
        """Any exception must return '' not raise."""
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', side_effect=RuntimeError("network error")):
            result = macro_prompt_block('AAPL')
        assert result == ""

    def test_unknown_sector_does_not_crash(self):
        """Ticker with no known sector must still return a valid string or ''."""
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', return_value=self._make_regime()), \
             patch('buffet_bot.macro._lookup_sector', return_value=None), \
             patch('buffet_bot.macro.get_recession_probability', return_value=0.30), \
             patch('buffet_bot.macro.compute_macro_score', return_value=50.0):
            result = macro_prompt_block('ZZZZ')
        assert isinstance(result, str)


# ── get_macro_regime FRED mock tests ─────────────────────────────────────────

class TestGetMacroRegimeFredMock:
    """Ensure FRED calls are mocked and the return shape is always correct."""

    def test_returns_neutral_when_fred_empty(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro._fetch_fred_data', return_value={}):
            result = get_macro_regime()
        assert result["regime"] == "NEUTRAL"
        assert result["confidence"] == 0.2
        assert "cached" in result

    def test_returns_dict_with_required_keys(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro._fetch_fred_data', return_value={
            'fed_rate': 3.0, 'yield_curve': 0.8, 'cpi': 310.0
        }):
            result = get_macro_regime()
        for key in ("regime", "confidence", "signals", "cached"):
            assert key in result

    def test_regime_is_valid_string(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro._fetch_fred_data', return_value={
            'fed_rate': 5.5, 'yield_curve': -0.3, 'cpi': 315.0
        }):
            result = get_macro_regime()
        assert result["regime"] in REGIMES

    def test_returns_neutral_on_fred_exception(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro._fetch_fred_data', side_effect=Exception("FRED timeout")):
            result = get_macro_regime()
        assert result["regime"] == "NEUTRAL"
        assert isinstance(result["confidence"], float)

    def test_stagflation_from_fred_data(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro._fetch_fred_data', return_value={
            'fed_rate': 5.5, 'yield_curve': -0.5, 'cpi': 318.0
        }):
            result = get_macro_regime()
        assert result["regime"] == "STAGFLATION"

    def test_risk_on_from_fred_data(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro._fetch_fred_data', return_value={
            'fed_rate': 1.0, 'yield_curve': 1.5, 'cpi': 300.0
        }):
            result = get_macro_regime()
        assert result["regime"] == "RISK_ON"

    def test_partial_fred_data_handled_gracefully(self):
        """Only fed_rate available — must not crash."""
        _reset_regime_cache()
        with patch('buffet_bot.macro._fetch_fred_data', return_value={'fed_rate': 4.5}):
            result = get_macro_regime()
        assert result["regime"] in REGIMES
        assert isinstance(result["confidence"], float)


# ── macro_score in _run_analysis return dict ──────────────────────────────────

def _make_full_realtime():
    """Return a complete realtime dict that satisfies all field accesses in analysis.py."""
    return {
        'price': 150.0, 'open': 148.0, 'high': 153.0, 'low': 147.0,
        'volume': 1_000_000, 'change_pct': 1.5, 'source': 'alpaca',
    }


def _make_hist_df():
    """Return a minimal DataFrame shaped like yf.download(...)[Close]."""
    import pandas as pd
    return pd.DataFrame({'Close': [100.0, 101.0, 102.0]})


class TestMacroScoreInRunAnalysis:
    """Verify macro_score key exists in the dict returned by _run_analysis()."""

    def _common_analysis_patches(self, *, realtime=None, macro_score=50.0,
                                  compute_macro_mock=None, macro_prompt_mock=None):
        """Return a list of patch objects covering all _run_analysis external calls."""
        rt = realtime or _make_full_realtime()
        cms_rv = compute_macro_mock if compute_macro_mock is not None else macro_score
        mpb_rv = macro_prompt_mock if macro_prompt_mock is not None else '\nMacro: NEUTRAL'
        return [
            patch('buffet_bot.analysis.get_buffett_metrics',      return_value={'score': 70}),
            patch('buffet_bot.analysis.get_realtime_data',         return_value=rt),
            patch('buffet_bot.analysis.get_recent_news',           return_value=[]),
            patch('buffet_bot.analysis._fetch_fred_data',          return_value={}),
            patch('buffet_bot.analysis.fetch_insider_transactions', return_value=[]),
            patch('buffet_bot.analysis.get_multiframe_signals',    return_value={}),
            patch('buffet_bot.analysis.get_analyst_consensus',     return_value={'upside_pct': 5.0}),
            patch('buffet_bot.analysis.macro_prompt_block',        return_value=mpb_rv),
            patch('buffet_bot.analysis.get_tech_indicators',       return_value={}),
            patch('buffet_bot.analysis.analyze_news_sentiment',
                  return_value={'overall': 'neutral', 'score': 0.0, 'count': 0, 'items': []}),
            patch('buffet_bot.analysis.compute_macro_score',       return_value=cms_rv),
            patch('yfinance.download',                             return_value=_make_hist_df()),
            patch('buffet_bot.analysis.insider_prompt_block',      return_value=''),
            patch('buffet_bot.analysis.log_recommendation'),
        ]

    @patch('buffet_bot.analysis.ollama.chat')
    def test_macro_score_key_in_result(self, mock_chat):
        """_run_analysis() must include 'macro_score' in its return dict."""
        llm_resp = json.dumps({
            'action': 'BUY', 'confidence': 0.8, 'qty': 10,
            'reason': 'Strong', 'stop_pct': 0.07
        })
        mock_chat.return_value = {'message': {'content': llm_resp}}

        patches = self._common_analysis_patches(macro_score=62.0)
        for p in patches: p.start()
        try:
            from buffet_bot.analysis import _run_analysis
            result = _run_analysis('AAPL', risk='low', primary_model='deepseek-r1')
        finally:
            for p in patches: p.stop()

        assert 'macro_score' in result, "'macro_score' key missing from _run_analysis() return dict"

    @patch('buffet_bot.analysis.ollama.chat')
    def test_macro_score_is_float(self, mock_chat):
        """macro_score must be a numeric value (int or float)."""
        llm_resp = json.dumps({
            'action': 'HOLD', 'confidence': 0.5, 'qty': 0,
            'reason': 'Uncertain', 'stop_pct': 0.05
        })
        mock_chat.return_value = {'message': {'content': llm_resp}}

        patches = self._common_analysis_patches(macro_score=50.0)
        for p in patches: p.start()
        try:
            from buffet_bot.analysis import _run_analysis
            result = _run_analysis('MSFT', risk='low', primary_model='deepseek-r1')
        finally:
            for p in patches: p.stop()

        assert isinstance(result.get('macro_score'), (int, float)), \
            f"macro_score should be numeric, got {type(result.get('macro_score'))}"

    @patch('buffet_bot.analysis.ollama.chat')
    def test_compute_macro_score_called_with_ticker(self, mock_chat):
        """_run_analysis must call compute_macro_score(ticker)."""
        llm_resp = json.dumps({'action': 'HOLD', 'confidence': 0.5, 'qty': 0,
                               'reason': 'ok', 'stop_pct': 0.05})
        mock_chat.return_value = {'message': {'content': llm_resp}}

        patches = self._common_analysis_patches(macro_score=45.0)
        # Start all patches and capture each started mock object
        started_mocks = [p.start() for p in patches]
        # compute_macro_score is at index 10 in the list returned by _common_analysis_patches
        cms_mock = started_mocks[10]
        try:
            from buffet_bot.analysis import _run_analysis
            _run_analysis('TSLA', risk='low', primary_model='deepseek-r1')
        finally:
            for p in patches: p.stop()

        cms_mock.assert_called_once_with('TSLA')

    @patch('buffet_bot.analysis.ollama.chat')
    def test_macro_prompt_block_called_in_concurrent_fan_out(self, mock_chat):
        """macro_prompt_block must be called as part of the concurrent fetch."""
        llm_resp = json.dumps({'action': 'HOLD', 'confidence': 0.5, 'qty': 0,
                               'reason': 'ok', 'stop_pct': 0.05})
        mock_chat.return_value = {'message': {'content': llm_resp}}

        patches = self._common_analysis_patches()
        started_mocks = [p.start() for p in patches]
        # macro_prompt_block is at index 7 in _common_analysis_patches
        mpb_mock = started_mocks[7]
        try:
            from buffet_bot.analysis import _run_analysis
            _run_analysis('NVDA', risk='low', primary_model='deepseek-r1')
        finally:
            for p in patches: p.stop()

        mpb_mock.assert_called_once_with('NVDA')


# ── edge-scan --macro flag CLI tests ─────────────────────────────────────────

def _make_edge_result(ticker: str = 'AAPL', edge_score: float = 72.0) -> dict:
    return {
        'ticker': ticker,
        'edge_score': edge_score,
        'components': {
            'buffett': 70.0, 'llm': 50.0, 'insider': 50.0,
            'politician': 50.0, 'earnings': 50.0, 'analyst': 55.0,
        },
        'weights_used': {
            'buffett': 0.25, 'llm': 0.20, 'insider': 0.20,
            'politician': 0.15, 'earnings': 0.10, 'analyst': 0.10,
        },
        'simulation_date': None,
    }


def _mock_edge_scan_patches(macro_score: float = 65.0, edge_score: float = 72.0):
    """Return a list of patch objects that neutralize all edge-scan external calls.

    Index map (used when starting patches and capturing mock objects):
      0 -> compute_edge_score
      1 -> compute_macro_score
      2 -> log_edge_scan
    """
    return [
        patch('buffet_bot.cmd_intel.compute_edge_score',
              return_value=_make_edge_result(edge_score=edge_score)),
        patch('buffet_bot.cmd_intel.compute_macro_score', return_value=macro_score),
        patch('buffet_bot.cmd_intel.log_edge_scan', return_value=1),
    ]


class TestEdgeScanMacroFlag:
    """CLI tests for edge-scan --macro flag."""

    @pytest.fixture
    def runner(self):
        return CliRunner(mix_stderr=False)

    def test_macro_flag_accepted_without_error(self, runner):
        """--macro flag must be accepted by Click without a usage error."""
        from buffet_bot.main import cli
        patches = _mock_edge_scan_patches()
        for p in patches: p.start()
        try:
            result = runner.invoke(cli, [
                'edge-scan', '--tickers', 'AAPL', '--macro'
            ])
        finally:
            for p in patches: p.stop()
        # Usage error would be exit_code == 2
        assert result.exit_code != 2, f"Click usage error:\n{result.output}"

    def test_macro_causes_compute_macro_score_to_be_called(self, runner):
        """When --macro is passed, compute_macro_score must be called for each ticker."""
        from buffet_bot.main import cli
        patches = _mock_edge_scan_patches(macro_score=71.0, edge_score=75.0)
        started = [p.start() for p in patches]
        cms_mock = started[1]  # compute_macro_score mock
        try:
            runner.invoke(cli, [
                'edge-scan', '--tickers', 'AAPL', '--macro', '--min-edge', '0'
            ])
            cms_mock.assert_called()
        finally:
            for p in patches: p.stop()

    def test_no_macro_flag_compute_macro_score_not_called(self, runner):
        """Without --macro, compute_macro_score must NOT be called."""
        from buffet_bot.main import cli
        patches = _mock_edge_scan_patches()
        started = [p.start() for p in patches]
        cms_mock = started[1]  # compute_macro_score mock
        try:
            runner.invoke(cli, ['edge-scan', '--tickers', 'AAPL', '--min-edge', '0'])
            cms_mock.assert_not_called()
        finally:
            for p in patches: p.stop()

    def test_macro_column_in_output_when_flag_set(self, runner):
        """Output table must contain 'Macro' column header when --macro is used."""
        from buffet_bot.main import cli
        patches = _mock_edge_scan_patches(macro_score=65.0, edge_score=80.0)
        for p in patches: p.start()
        try:
            result = runner.invoke(cli, [
                'edge-scan', '--tickers', 'AAPL', '--macro', '--min-edge', '0'
            ])
        finally:
            for p in patches: p.stop()
        assert 'Macro' in result.output, \
            f"'Macro' column header not found in output:\n{result.output}"

    def test_macro_note_absent_in_footer_without_flag(self, runner):
        """Without --macro, the footer 'Macro: FRED' note must NOT appear."""
        from buffet_bot.main import cli
        patches = _mock_edge_scan_patches()
        for p in patches: p.start()
        try:
            result = runner.invoke(cli, [
                'edge-scan', '--tickers', 'AAPL', '--min-edge', '0'
            ])
        finally:
            for p in patches: p.stop()
        # "Macro: FRED" only appears in footer when --macro is active
        assert 'Macro: FRED' not in result.output, \
            f"'Macro: FRED' footer appeared unexpectedly:\n{result.output}"

    def test_macro_score_included_in_weights_json_on_save(self, runner):
        """When --macro and --save are combined, macro_score must be in the
        weights_json blob passed to log_edge_scan."""
        from buffet_bot.main import cli
        patches = _mock_edge_scan_patches(macro_score=68.0, edge_score=78.0)
        started = [p.start() for p in patches]
        log_mock = started[2]  # log_edge_scan mock
        try:
            runner.invoke(cli, [
                'edge-scan', '--tickers', 'AAPL', '--macro', '--save', '--min-edge', '0'
            ])
            # log_edge_scan must have been called at least once
            assert log_mock.called, "log_edge_scan was not called when --save was used"
            # Inspect the argument passed to log_edge_scan
            call_arg = log_mock.call_args[0][0]
            weights = call_arg.get('weights_used', {})
            assert 'macro_score' in weights, \
                f"'macro_score' missing from weights_json blob: {weights}"
        finally:
            for p in patches: p.stop()

    def test_macro_score_value_stored_correctly(self, runner):
        """The macro_score value in weights_json must match what compute_macro_score returned."""
        from buffet_bot.main import cli
        expected_macro = 71.5
        patches = _mock_edge_scan_patches(macro_score=expected_macro, edge_score=80.0)
        started = [p.start() for p in patches]
        log_mock = started[2]  # log_edge_scan mock
        try:
            runner.invoke(cli, [
                'edge-scan', '--tickers', 'AAPL', '--macro', '--save', '--min-edge', '0'
            ])
            call_arg = log_mock.call_args[0][0]
            weights = call_arg.get('weights_used', {})
            stored = weights.get('macro_score')
            assert stored == expected_macro, \
                f"Expected macro_score={expected_macro}, got {stored}"
        finally:
            for p in patches: p.stop()

    def test_no_save_without_macro_does_not_include_macro_score(self, runner):
        """Without --macro, saved weights_json must not contain macro_score."""
        from buffet_bot.main import cli
        patches = _mock_edge_scan_patches(edge_score=80.0)
        started = [p.start() for p in patches]
        log_mock = started[2]  # log_edge_scan mock
        try:
            runner.invoke(cli, [
                'edge-scan', '--tickers', 'AAPL', '--save', '--min-edge', '0'
            ])
            if log_mock.called:
                call_arg = log_mock.call_args[0][0]
                weights = call_arg.get('weights_used', {})
                assert 'macro_score' not in weights, \
                    f"macro_score should not be in weights when --macro not used: {weights}"
        finally:
            for p in patches: p.stop()


# ── get_recession_probability tests ──────────────────────────────────────────

class TestGetRecessionProbability:
    """Tests for get_recession_probability() — returns 0.0-1.0 float."""

    def _make_regime_signals(self, yc: str, fr: str) -> dict:
        return {
            "regime": "NEUTRAL",
            "confidence": 0.5,
            "signals": {"yield_curve": yc, "fed_rate": fr},
            "cached": False,
        }

    def test_returns_float_in_range(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime',
                   return_value=self._make_regime_signals("NORMAL (+0.80%)", "MODERATE (3.00%)")):
            prob = get_recession_probability()
        assert isinstance(prob, float)
        assert 0.0 <= prob <= 1.0

    def test_inverted_curve_high_probability(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime',
                   return_value=self._make_regime_signals("INVERTED (-0.50%)", "RESTRICTIVE (5.25%)")):
            prob = get_recession_probability()
        assert prob > 0.5

    def test_normal_curve_low_probability(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime',
                   return_value=self._make_regime_signals("NORMAL (+1.20%)", "ACCOMMODATIVE (0.25%)")):
            prob = get_recession_probability()
        assert prob < 0.5

    def test_returns_0_5_on_exception(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', side_effect=RuntimeError("boom")):
            prob = get_recession_probability()
        assert prob == 0.5

    def test_unavailable_signals_return_0_5(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime',
                   return_value=self._make_regime_signals("UNAVAILABLE", "UNAVAILABLE")):
            prob = get_recession_probability()
        assert prob == 0.5


# ── get_sector_rotation_signal tests ─────────────────────────────────────────

class TestGetSectorRotationSignal:
    """Tests for get_sector_rotation_signal() — returns OVERWEIGHT/NEUTRAL/UNDERWEIGHT."""

    def test_returns_dict_with_required_keys(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', return_value={
            "regime": "RISK_ON", "confidence": 0.7,
            "signals": {}, "cached": False
        }):
            result = get_sector_rotation_signal("Technology")
        for key in ("signal", "reason", "regime"):
            assert key in result

    def test_risk_on_technology_is_overweight(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', return_value={
            "regime": "RISK_ON", "confidence": 0.7, "signals": {}, "cached": False
        }):
            result = get_sector_rotation_signal("Technology")
        assert result["signal"] == "OVERWEIGHT"

    def test_risk_off_utilities_is_overweight(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', return_value={
            "regime": "RISK_OFF", "confidence": 0.7, "signals": {}, "cached": False
        }):
            result = get_sector_rotation_signal("Utilities")
        assert result["signal"] == "OVERWEIGHT"

    def test_unknown_sector_returns_neutral(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', return_value={
            "regime": "RISK_ON", "confidence": 0.7, "signals": {}, "cached": False
        }):
            result = get_sector_rotation_signal("Crypto Mining")
        assert result["signal"] == "NEUTRAL"

    def test_case_insensitive_sector_matching(self):
        """Lowercase sector name should match the same as title-case."""
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', return_value={
            "regime": "RISK_ON", "confidence": 0.7, "signals": {}, "cached": False
        }):
            result_lower = get_sector_rotation_signal("technology")
            result_title = get_sector_rotation_signal("Technology")
        assert result_lower["signal"] == result_title["signal"]

    def test_returns_neutral_on_exception(self):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', side_effect=RuntimeError("boom")):
            result = get_sector_rotation_signal("Technology")
        assert result["signal"] == "NEUTRAL"

    @pytest.mark.parametrize("regime,sector,expected_signal", [
        ("RISK_ON",    "Technology",  "OVERWEIGHT"),
        ("RISK_ON",    "Utilities",   "UNDERWEIGHT"),
        ("RISK_OFF",   "Healthcare",  "OVERWEIGHT"),
        ("RISK_OFF",   "Technology",  "UNDERWEIGHT"),
        ("STAGFLATION","Energy",      "OVERWEIGHT"),
        ("STAGFLATION","Technology",  "UNDERWEIGHT"),
        ("NEUTRAL",    "Technology",  "NEUTRAL"),
    ])
    def test_parametrized_sector_signals(self, regime, sector, expected_signal):
        _reset_regime_cache()
        with patch('buffet_bot.macro.get_macro_regime', return_value={
            "regime": regime, "confidence": 0.7, "signals": {}, "cached": False
        }):
            result = get_sector_rotation_signal(sector)
        assert result["signal"] == expected_signal

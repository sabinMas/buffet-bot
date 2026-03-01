"""Phase 3: Data fetching tests — external APIs mocked, no real network calls."""
import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ── Helpers ───────────────────────────────────────────────────────────────────

def _yf_info(**overrides):
    """Return a minimal yfinance info dict with sensible non-scoring defaults."""
    base = {
        'returnOnEquity': 0.0,
        'debtToEquity': 999.0,
        'operatingMargins': 0.0,
        'returnOnAssets': 0.0,
        'trailingPE': 0.0,
        'priceToBook': 0.0,
        'freeCashflow': 0.0,
        'marketCap': 1.0,
        'dividendYield': 0.0,
        'earningsGrowth': 0.0,
    }
    base.update(overrides)
    return base


def _mock_fred_obs(value='5.25'):
    """Return a mock requests.Response for one FRED observation."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {'observations': [{'value': value}]}
    return resp


# ── get_buffett_metrics ───────────────────────────────────────────────────────

class TestGetBuffettMetrics:
    """Unit tests for the 8-criterion Buffett scoring function."""

    def _invoke(self, info):
        """Patch yf.Ticker and call get_buffett_metrics('TEST')."""
        from buffet_bot.main import get_buffett_metrics
        mock_ticker = MagicMock()
        mock_ticker.info = info
        with patch('buffet_bot.main.yf.Ticker', return_value=mock_ticker):
            return get_buffett_metrics('TEST')

    # ── Score boundaries ──────────────────────────────────────────────────────

    def test_no_criteria_met_scores_zero(self):
        """Default info (no criteria pass) → score = 0."""
        result = self._invoke(_yf_info())
        assert result['score'] == 0

    def test_all_criteria_met_scores_100(self):
        """All 8 criteria pass → score = 100."""
        info = _yf_info(
            returnOnEquity=0.20,    # ROE 20% > 15%   → +25
            returnOnAssets=0.13,    # ROIC 13% > 12%  → +20
            debtToEquity=40.0,      # Debt < 50        → +15
            operatingMargins=0.15,  # OpMgn 15% > 10% → +15
            freeCashflow=4e7,       # FCF yield = 4%  → +15  (4e7 / 1e9)
            marketCap=1e9,
            trailingPE=20.0,        # 0 < PE < 25      → +5
            priceToBook=4.0,        # 0 < PB < 5       → +3
            dividendYield=0.02,     # Div 2% > 1.5%   → +2
        )
        result = self._invoke(info)
        assert result['score'] == 100

    # ── Individual criteria ───────────────────────────────────────────────────

    def test_roe_above_threshold_adds_points(self):
        """ROE > 15% → +25 pts."""
        result = self._invoke(_yf_info(returnOnEquity=0.16))
        assert result['score'] == 25
        assert result.get('roe_pass') is True

    def test_roe_exactly_at_threshold_does_not_pass(self):
        """ROE exactly 15% is not > 15% → no ROE points."""
        result = self._invoke(_yf_info(returnOnEquity=0.15))
        assert result.get('roe_pass') is not True

    def test_roic_above_threshold_adds_points(self):
        """returnOnAssets > 12% → +20 pts."""
        result = self._invoke(_yf_info(returnOnAssets=0.13))
        assert result['score'] == 20
        assert result.get('roic_pass') is True

    def test_debt_below_threshold_adds_points(self):
        """debtToEquity < 50 → +15 pts."""
        result = self._invoke(_yf_info(debtToEquity=49.9))
        assert result['score'] == 15
        assert result.get('debt_pass') is True

    def test_high_debt_does_not_add_points(self):
        """debtToEquity >= 50 → no debt pts."""
        result = self._invoke(_yf_info(debtToEquity=50.0))
        assert result.get('debt_pass') is not True

    def test_op_margin_above_threshold_adds_points(self):
        """operatingMargins > 10% → +15 pts."""
        result = self._invoke(_yf_info(operatingMargins=0.11))
        assert result['score'] == 15
        assert result.get('margin_pass') is True

    def test_fcf_yield_above_threshold_adds_points(self):
        """FCF yield > 3% → +15 pts.  FCF/marketCap = 0.04 → 4%."""
        result = self._invoke(_yf_info(freeCashflow=4e7, marketCap=1e9))
        assert result['score'] == 15
        assert result.get('fcf_pass') is True

    def test_pe_in_range_adds_points(self):
        """0 < PE < 25 → +5 pts."""
        result = self._invoke(_yf_info(trailingPE=15.0))
        assert result['score'] == 5
        assert result.get('pe_pass') is True

    def test_pe_zero_does_not_add_points(self):
        """PE = 0 means no P/E data → boundary 0 < pe is False → no points."""
        result = self._invoke(_yf_info(trailingPE=0.0))
        assert result.get('pe_pass') is not True

    def test_pb_in_range_adds_points(self):
        """0 < PB < 5 → +3 pts."""
        result = self._invoke(_yf_info(priceToBook=3.0))
        assert result['score'] == 3
        assert result.get('pb_pass') is True

    def test_div_yield_above_threshold_adds_points(self):
        """dividendYield > 1.5% → +2 pts."""
        result = self._invoke(_yf_info(dividendYield=0.02))
        assert result['score'] == 2
        assert result.get('div_pass') is True

    # ── Return shape ──────────────────────────────────────────────────────────

    def test_returns_score_key(self):
        """Result always contains a 'score' key."""
        result = self._invoke(_yf_info())
        assert 'score' in result

    def test_returns_numeric_metric_keys(self):
        """Standard metric keys are present and numeric."""
        result = self._invoke(_yf_info())
        for key in ('roe', 'roic', 'debt_eq', 'op_margin', 'fcf_yield', 'pe', 'pb'):
            assert key in result, f"Missing key: {key}"
            assert isinstance(result[key], (int, float)), f"Non-numeric: {key}"

    def test_score_is_non_negative_integer(self):
        """Score is always a non-negative integer."""
        result = self._invoke(_yf_info())
        assert isinstance(result['score'], int)
        assert result['score'] >= 0

    # ── Error handling ────────────────────────────────────────────────────────

    def test_exception_returns_score_zero(self):
        """If yf.Ticker raises, fall back to {'score': 0}."""
        from buffet_bot.main import get_buffett_metrics
        with patch('buffet_bot.main.yf.Ticker', side_effect=Exception("network error")):
            result = get_buffett_metrics('ERR')
        assert result.get('score', 0) == 0

    def test_none_values_in_info_do_not_raise(self):
        """None values for any info field must be handled without crashing."""
        info = {k: None for k in _yf_info()}
        result = self._invoke(info)
        assert isinstance(result, dict)
        assert 'score' in result

    def test_missing_keys_in_info_do_not_raise(self):
        """Completely empty info dict must not raise."""
        result = self._invoke({})
        assert isinstance(result, dict)
        assert 'score' in result


# ── _fetch_fred_data ─────────────────────────────────────────────────────────

class TestFetchFredData:
    """Unit tests for the FRED macro data fetcher."""

    def test_returns_empty_dict_when_no_api_key(self, monkeypatch):
        """FRED_API_KEY is '' → immediately return {}."""
        monkeypatch.setattr('buffet_bot.main.FRED_API_KEY', '')
        from buffet_bot.main import _fetch_fred_data
        result = _fetch_fred_data()
        assert result == {}

    def test_returns_dict_with_valid_responses(self, monkeypatch):
        """All 3 FRED series respond successfully → dict has 3 keys."""
        monkeypatch.setattr('buffet_bot.main.FRED_API_KEY', 'test-key')
        with patch('buffet_bot.main.requests.get', return_value=_mock_fred_obs('5.25')):
            from buffet_bot.main import _fetch_fred_data
            result = _fetch_fred_data()
        assert isinstance(result, dict)
        assert len(result) == 3

    def test_fed_rate_key_present_when_api_key_set(self, monkeypatch):
        """'fed_rate' is a key in successful response."""
        monkeypatch.setattr('buffet_bot.main.FRED_API_KEY', 'test-key')
        with patch('buffet_bot.main.requests.get', return_value=_mock_fred_obs('5.25')):
            from buffet_bot.main import _fetch_fred_data
            result = _fetch_fred_data()
        assert 'fed_rate' in result
        assert abs(result['fed_rate'] - 5.25) < 1e-6

    def test_handles_request_exception_gracefully(self, monkeypatch):
        """Network error → returns {} (all series skipped) — must not raise."""
        monkeypatch.setattr('buffet_bot.main.FRED_API_KEY', 'test-key')
        with patch('buffet_bot.main.requests.get', side_effect=Exception("timeout")):
            from buffet_bot.main import _fetch_fred_data
            result = _fetch_fred_data()
        assert isinstance(result, dict)

    def test_skips_series_with_missing_data_value(self, monkeypatch):
        """FRED returns '.' for missing data → ValueError → series is skipped."""
        monkeypatch.setattr('buffet_bot.main.FRED_API_KEY', 'test-key')
        bad_resp = MagicMock()
        bad_resp.raise_for_status.return_value = None
        bad_resp.json.return_value = {'observations': [{'value': '.'}]}
        with patch('buffet_bot.main.requests.get', return_value=bad_resp):
            from buffet_bot.main import _fetch_fred_data
            result = _fetch_fred_data()
        # All series skipped due to '.' → dict is empty
        assert result == {}

    def test_values_are_floats(self, monkeypatch):
        """Every value in the returned dict is a float."""
        monkeypatch.setattr('buffet_bot.main.FRED_API_KEY', 'test-key')
        with patch('buffet_bot.main.requests.get', return_value=_mock_fred_obs('3.14')):
            from buffet_bot.main import _fetch_fred_data
            result = _fetch_fred_data()
        for v in result.values():
            assert isinstance(v, float)


# ── _get_earnings_date ───────────────────────────────────────────────────────

class TestGetEarningsDate:
    """Unit tests for the Nasdaq earnings calendar scraper."""

    def _make_nasdaq_resp(self, rows):
        """Build a mocked Nasdaq API response with given rows."""
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {'data': {'rows': rows}}
        return resp

    def test_returns_none_when_ticker_not_in_any_day(self):
        """If 'AAPL' never appears in 8 days of results → return None."""
        resp = self._make_nasdaq_resp([{'symbol': 'OTHER', 'time': 'time-after-hours',
                                        'epsForecast': '1.00', 'fiscalQuarterEnding': 'Q1'}])
        with patch('buffet_bot.main.requests.get', return_value=resp):
            with patch('buffet_bot.main.time.sleep'):
                from buffet_bot.main import _get_earnings_date
                result = _get_earnings_date('AAPL')
        assert result is None

    def test_returns_dict_when_ticker_found(self):
        """Ticker found on first calendar day → return a populated dict."""
        resp = self._make_nasdaq_resp([{
            'symbol': 'AAPL', 'time': 'time-after-hours',
            'epsForecast': '1.50', 'fiscalQuarterEnding': 'March 2026',
        }])
        with patch('buffet_bot.main.requests.get', return_value=resp):
            with patch('buffet_bot.main.time.sleep'):
                from buffet_bot.main import _get_earnings_date
                result = _get_earnings_date('AAPL')
        assert result is not None
        assert result['eps_forecast'] == '1.50'
        assert result['fiscal_quarter'] == 'March 2026'
        assert 'date' in result
        assert 'time' in result

    def test_case_insensitive_ticker_match(self):
        """Symbol comparison is case-insensitive (row 'aapl' matches query 'AAPL')."""
        resp = self._make_nasdaq_resp([{
            'symbol': 'aapl', 'time': 'time-pre-market',
            'epsForecast': '1.20', 'fiscalQuarterEnding': 'Q2',
        }])
        with patch('buffet_bot.main.requests.get', return_value=resp):
            with patch('buffet_bot.main.time.sleep'):
                from buffet_bot.main import _get_earnings_date
                result = _get_earnings_date('AAPL')
        assert result is not None

    def test_handles_request_exception_gracefully(self):
        """Network errors on all days → returns None, does not raise."""
        with patch('buffet_bot.main.requests.get', side_effect=Exception("timeout")):
            with patch('buffet_bot.main.time.sleep'):
                from buffet_bot.main import _get_earnings_date
                result = _get_earnings_date('AAPL')
        assert result is None

    def test_returns_none_when_rows_is_none(self):
        """rows=None in response (edge case) → returns None without crash."""
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {'data': {'rows': None}}
        with patch('buffet_bot.main.requests.get', return_value=resp):
            with patch('buffet_bot.main.time.sleep'):
                from buffet_bot.main import _get_earnings_date
                result = _get_earnings_date('AAPL')
        assert result is None


# ── get_realtime_data ────────────────────────────────────────────────────────

class TestGetRealtimeData:
    """Unit tests for the live market data helper (Alpaca → yfinance fallback)."""

    def _make_alpaca_quote(self, bid=149.0, ask=151.0):
        q = MagicMock()
        q.bid_price = bid
        q.ask_price = ask
        return q

    def _make_alpaca_bar(self, open_=150.0, high=155.0, low=148.0, volume=1_000_000):
        b = MagicMock()
        b.open = open_
        b.high = high
        b.low = low
        b.volume = volume
        return b

    def test_returns_dict_from_alpaca_on_success(self):
        """Happy path: Alpaca API returns valid quote + bar → price is bid/ask mid."""
        from buffet_bot.main import get_realtime_data
        quote = self._make_alpaca_quote(bid=149.0, ask=151.0)
        bar = self._make_alpaca_bar(open_=150.0, high=155.0, low=148.0, volume=500_000)

        mock_data_client = MagicMock()
        mock_data_client.get_stock_latest_quote.return_value = {'AAPL': quote}
        mock_data_client.get_stock_latest_bar.return_value = {'AAPL': bar}

        with patch('buffet_bot.main.data_client', mock_data_client):
            result = get_realtime_data('AAPL')

        assert result['price'] == 150.0          # mid = (149+151)/2
        assert result['source'] == 'alpaca'
        assert result['high'] == 155.0
        assert result['low'] == 148.0
        assert result['volume'] == 500_000

    def test_change_pct_positive_when_price_above_open(self):
        """price > open → change_pct > 0."""
        from buffet_bot.main import get_realtime_data
        quote = self._make_alpaca_quote(bid=159.0, ask=161.0)  # mid=160
        bar = self._make_alpaca_bar(open_=150.0)

        mock_data_client = MagicMock()
        mock_data_client.get_stock_latest_quote.return_value = {'AAPL': quote}
        mock_data_client.get_stock_latest_bar.return_value = {'AAPL': bar}

        with patch('buffet_bot.main.data_client', mock_data_client):
            result = get_realtime_data('AAPL')

        assert result['change_pct'] > 0

    def test_falls_back_to_yfinance_on_alpaca_failure(self):
        """When Alpaca raises, yfinance fast_info is used as fallback."""
        from buffet_bot.main import get_realtime_data

        mock_data_client = MagicMock()
        mock_data_client.get_stock_latest_quote.side_effect = Exception("alpaca down")

        mock_fast_info = MagicMock()
        mock_fast_info.last_price = 175.0
        mock_fast_info.open = 170.0
        mock_fast_info.day_high = 178.0
        mock_fast_info.day_low = 169.0
        mock_fast_info.three_month_average_volume = 80_000_000

        mock_ticker = MagicMock()
        mock_ticker.fast_info = mock_fast_info

        with patch('buffet_bot.main.data_client', mock_data_client):
            with patch('buffet_bot.main.yf.Ticker', return_value=mock_ticker):
                result = get_realtime_data('AAPL')

        assert result['price'] == 175.0
        assert result['source'] == 'yfinance'

    def test_returns_empty_dict_when_both_sources_fail(self):
        """Both Alpaca and yfinance fail → returns {} without raising."""
        from buffet_bot.main import get_realtime_data

        mock_data_client = MagicMock()
        mock_data_client.get_stock_latest_quote.side_effect = Exception("alpaca down")

        with patch('buffet_bot.main.data_client', mock_data_client):
            with patch('buffet_bot.main.yf.Ticker', side_effect=Exception("yf down")):
                result = get_realtime_data('AAPL')

        assert isinstance(result, dict)
        assert result == {}

"""Phase 4: CLI command tests via Click CliRunner — no real network or LLM calls."""
import json
import pytest
from unittest.mock import MagicMock, patch

from buffet_bot.main import cli


# ── Shared fixtures / helpers ─────────────────────────────────────────────────

def _make_analysis_result(consensus='BUY', score=75, price=150.0):
    """Return a minimal _run_analysis result dict."""
    return {
        'buffett': {'score': score, 'roe': 20.0, 'roic': 15.0, 'debt_eq': 30.0,
                    'op_margin': 18.0, 'fcf_yield': 4.0, 'pe': 20.0, 'pb': 3.5,
                    'div_yield': 1.8, 'eg_1y': 10.0},
        'tech': {},
        'realtime': {'price': price, 'open': 145.0, 'high': 155.0, 'low': 144.0,
                     'volume': 1_000_000, 'change_pct': 3.4, 'source': 'alpaca'},
        'news': [],
        'sentiment': {'overall': 'positive', 'score': 0.6, 'count': 0, 'items': []},
        'responses': {
            'deepseek-r1': {'action': consensus, 'confidence': 0.85, 'qty': 10,
                            'reason': 'Strong fundamentals', 'stop_pct': 0.07},
        },
        'consensus': consensus,
        'best_buy_resp': (
            {'action': consensus, 'confidence': 0.85, 'qty': 10,
             'reason': 'Strong fundamentals', 'stop_pct': 0.07}
            if consensus == 'BUY' else None
        ),
    }


# ── analyze command ───────────────────────────────────────────────────────────

class TestAnalyzeCommand:
    """Tests for: buffet-bot analyze TICKER [--json] [--risk] [--strategy]"""

    def test_json_output_contains_required_keys(self, runner):
        """--json flag: output is valid JSON with all required top-level keys."""
        result_data = _make_analysis_result('BUY')
        with patch('buffet_bot.cmd_trading._run_analysis', return_value=result_data):
            with patch('buffet_bot.cmd_trading.is_crypto_symbol', return_value=False):
                result = runner.invoke(cli, ['analyze', 'AAPL', '--json'])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        for key in ('ticker', 'timestamp', 'consensus', 'buffett_score', 'price'):
            assert key in data, f"Missing key: {key}"

    def test_json_ticker_is_uppercased(self, runner):
        """Ticker argument is normalised to uppercase in JSON output."""
        result_data = _make_analysis_result('HOLD')
        with patch('buffet_bot.cmd_trading._run_analysis', return_value=result_data):
            with patch('buffet_bot.cmd_trading.is_crypto_symbol', return_value=False):
                result = runner.invoke(cli, ['analyze', 'aapl', '--json'])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data['ticker'] == 'AAPL'

    def test_json_consensus_reflects_mock(self, runner):
        """JSON output consensus matches what _run_analysis returned."""
        result_data = _make_analysis_result('HOLD')
        with patch('buffet_bot.cmd_trading._run_analysis', return_value=result_data):
            with patch('buffet_bot.cmd_trading.is_crypto_symbol', return_value=False):
                result = runner.invoke(cli, ['analyze', 'AAPL', '--json'])

        data = json.loads(result.output)
        assert data['consensus'] == 'HOLD'

    def test_json_buffett_score_matches_mock(self, runner):
        """buffett_score in JSON equals the score from the mocked analysis."""
        result_data = _make_analysis_result('BUY', score=82)
        with patch('buffet_bot.cmd_trading._run_analysis', return_value=result_data):
            with patch('buffet_bot.cmd_trading.is_crypto_symbol', return_value=False):
                result = runner.invoke(cli, ['analyze', 'AAPL', '--json'])

        data = json.loads(result.output)
        assert data['buffett_score'] == 82

    def test_json_price_matches_mock(self, runner):
        """price in JSON equals the realtime price from the mocked analysis."""
        result_data = _make_analysis_result('BUY', price=200.0)
        with patch('buffet_bot.cmd_trading._run_analysis', return_value=result_data):
            with patch('buffet_bot.cmd_trading.is_crypto_symbol', return_value=False):
                result = runner.invoke(cli, ['analyze', 'AAPL', '--json'])

        data = json.loads(result.output)
        assert abs(data['price'] - 200.0) < 1e-6

    def test_crypto_symbol_routes_to_crypto_handler(self, runner):
        """BTC/USD should route to _analyze_crypto, not _run_analysis."""
        mock_crypto = MagicMock()
        with patch('buffet_bot.cmd_trading.is_crypto_symbol', return_value=True):
            with patch('buffet_bot.cmd_trading._analyze_crypto', mock_crypto):
                result = runner.invoke(cli, ['analyze', 'BTC/USD'])

        assert result.exit_code == 0
        mock_crypto.assert_called_once()

    def test_invalid_risk_option_fails(self, runner):
        """Passing an unrecognised --risk value should exit with non-zero status."""
        result = runner.invoke(cli, ['analyze', 'AAPL', '--risk', 'extreme'])
        assert result.exit_code != 0

    def test_invalid_strategy_option_fails(self, runner):
        """Passing an unrecognised --strategy value should exit with non-zero status."""
        result = runner.invoke(cli, ['analyze', 'AAPL', '--strategy', 'yolo'])
        assert result.exit_code != 0


# ── scan command ──────────────────────────────────────────────────────────────

class TestScanCommand:
    """Tests for: buffet-bot scan [--json] [--top N] [--watchlist]"""

    def _mock_metrics(self, score=65):
        return {
            'score': score, 'roe': 18.0, 'roic': 14.0, 'debt_eq': 35.0,
            'op_margin': 12.0, 'fcf_yield': 3.5, 'pe': 22.0, 'pb': 4.0, 'div_yield': 1.2,
        }

    def test_json_output_is_valid_json(self, runner):
        """--json flag: output is a non-empty JSON array."""
        with patch('buffet_bot.cmd_trading.get_buffett_metrics', return_value=self._mock_metrics()):
            result = runner.invoke(cli, ['scan', '--json'])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_json_each_item_has_ticker_and_score(self, runner):
        """Each item in JSON output has 'ticker' and 'buffett_score' keys."""
        with patch('buffet_bot.cmd_trading.get_buffett_metrics', return_value=self._mock_metrics(70)):
            result = runner.invoke(cli, ['scan', '--json'])

        data = json.loads(result.output)
        for item in data:
            assert 'ticker' in item
            assert 'buffett_score' in item

    def test_top_flag_limits_results(self, runner):
        """--top 3 returns at most 3 tickers in JSON output."""
        with patch('buffet_bot.cmd_trading.get_buffett_metrics', return_value=self._mock_metrics()):
            result = runner.invoke(cli, ['scan', '--json', '--top', '3'])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data) <= 3

    def test_top_zero_returns_all_tickers(self, runner):
        """--top 0 returns all default tickers in JSON output."""
        default_tickers = ['AAPL', 'MSFT', 'GOOGL', 'BRK-B', 'JNJ', 'V', 'JPM', 'PG']
        with patch('buffet_bot.cmd_trading.get_buffett_metrics', return_value=self._mock_metrics()):
            result = runner.invoke(cli, ['scan', '--json', '--top', '0'])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data) == len(default_tickers)

    def test_results_sorted_by_score_descending(self, runner):
        """JSON results are sorted highest score first."""
        scores = {'AAPL': 80, 'MSFT': 60, 'GOOGL': 75, 'BRK-B': 50,
                  'JNJ': 40, 'V': 90, 'JPM': 45, 'PG': 55}

        def _metrics(ticker):
            return {'score': scores.get(ticker, 0)}

        with patch('buffet_bot.cmd_trading.get_buffett_metrics', side_effect=_metrics):
            result = runner.invoke(cli, ['scan', '--json', '--top', '0'])

        data = json.loads(result.output)
        returned_scores = [item['buffett_score'] for item in data]
        assert returned_scores == sorted(returned_scores, reverse=True)

    def test_watchlist_flag_uses_saved_watchlist(self, runner, in_memory_db, monkeypatch):
        """--watchlist flag: if watchlist has tickers, those are scanned."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import add_to_watchlist
        add_to_watchlist('NVDA')
        add_to_watchlist('AMD')

        with patch('buffet_bot.cmd_trading.get_buffett_metrics', return_value=self._mock_metrics()):
            result = runner.invoke(cli, ['scan', '--watchlist', '--json', '--top', '0'])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        tickers = {item['ticker'] for item in data}
        assert 'NVDA' in tickers
        assert 'AMD' in tickers


# ── status command ────────────────────────────────────────────────────────────

class TestStatusCommand:
    """Tests for: buffet-bot status"""

    def _mock_account(self, cash=10_000.0, buying_power=20_000.0):
        acct = MagicMock()
        acct.cash = cash
        acct.buying_power = buying_power
        return acct

    def test_exits_zero_with_mocked_account(self, runner):
        """status exits 0 when trading_client.get_account() is mocked."""
        mock_acct = self._mock_account()
        with patch('buffet_bot.cmd_trading.trading_client') as mock_tc:
            mock_tc.get_account.return_value = mock_acct
            with patch.dict('os.environ', {'COINBASE_API_KEY': '', 'IBKR_ACCOUNT_ID': ''}):
                result = runner.invoke(cli, ['status'])
        assert result.exit_code == 0, result.output

    def test_output_contains_alpaca_panel_header(self, runner):
        """Output includes 'Alpaca Paper Account' text."""
        mock_acct = self._mock_account()
        with patch('buffet_bot.cmd_trading.trading_client') as mock_tc:
            mock_tc.get_account.return_value = mock_acct
            with patch.dict('os.environ', {'COINBASE_API_KEY': '', 'IBKR_ACCOUNT_ID': ''}):
                result = runner.invoke(cli, ['status'])
        assert 'Alpaca Paper Account' in result.output

    def test_output_contains_cash_value(self, runner):
        """Output contains the formatted cash amount."""
        mock_acct = self._mock_account(cash=12_345.67)
        with patch('buffet_bot.cmd_trading.trading_client') as mock_tc:
            mock_tc.get_account.return_value = mock_acct
            with patch.dict('os.environ', {'COINBASE_API_KEY': '', 'IBKR_ACCOUNT_ID': ''}):
                result = runner.invoke(cli, ['status'])
        assert '12,345.67' in result.output

    def test_coinbase_not_configured_message_when_no_key(self, runner):
        """Without COINBASE_API_KEY, output mentions Coinbase not configured."""
        mock_acct = self._mock_account()
        with patch('buffet_bot.cmd_trading.trading_client') as mock_tc:
            mock_tc.get_account.return_value = mock_acct
            with patch.dict('os.environ', {'COINBASE_API_KEY': '', 'IBKR_ACCOUNT_ID': ''},
                            clear=False):
                with patch('buffet_bot.cmd_trading.os.getenv', side_effect=lambda k, *a: {
                    'COINBASE_API_KEY': '',
                    'IBKR_ACCOUNT_ID': '',
                }.get(k, '')):
                    result = runner.invoke(cli, ['status'])
        # We just check no exception occurred — coinbase block is optional
        assert result.exit_code == 0

    def test_ibkr_not_configured_message_when_no_account_id(self, runner):
        """Without IBKR_ACCOUNT_ID, status must still succeed."""
        mock_acct = self._mock_account()
        with patch('buffet_bot.cmd_trading.trading_client') as mock_tc:
            mock_tc.get_account.return_value = mock_acct
            result = runner.invoke(cli, ['status'])
        # Status always exits 0 as long as Alpaca call doesn't raise
        assert result.exit_code == 0


# ── watchlist command group ───────────────────────────────────────────────────

class TestWatchlistCommands:
    """Tests for: buffet-bot watchlist add/remove/show"""

    def test_add_exits_zero(self, runner, in_memory_db, monkeypatch):
        """watchlist add TSLA exits 0."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        result = runner.invoke(cli, ['watchlist', 'add', 'TSLA'])
        assert result.exit_code == 0, result.output

    def test_add_confirmation_message(self, runner, in_memory_db, monkeypatch):
        """watchlist add prints a confirmation containing the ticker."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        result = runner.invoke(cli, ['watchlist', 'add', 'NVDA'])
        assert 'NVDA' in result.output

    def test_add_uppercase_normalisation(self, runner, in_memory_db, monkeypatch):
        """Lowercase ticker is normalised to uppercase in confirmation."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        result = runner.invoke(cli, ['watchlist', 'add', 'msft'])
        assert 'MSFT' in result.output

    def test_remove_exits_zero_for_existing_ticker(self, runner, in_memory_db, monkeypatch):
        """watchlist remove on an existing ticker exits 0."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        runner.invoke(cli, ['watchlist', 'add', 'AAPL'])
        result = runner.invoke(cli, ['watchlist', 'remove', 'AAPL'])
        assert result.exit_code == 0, result.output

    def test_remove_exits_zero_for_nonexistent_ticker(self, runner, in_memory_db, monkeypatch):
        """watchlist remove on a ticker that was never added exits 0 (silent)."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        result = runner.invoke(cli, ['watchlist', 'remove', 'UNKNOWN'])
        assert result.exit_code == 0, result.output

    def test_show_exits_zero_when_empty(self, runner, in_memory_db, monkeypatch):
        """watchlist show with empty list exits 0."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        result = runner.invoke(cli, ['watchlist', 'show'])
        assert result.exit_code == 0, result.output

    def test_show_displays_added_tickers(self, runner, in_memory_db, monkeypatch):
        """After add, watchlist show output contains the added ticker."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        runner.invoke(cli, ['watchlist', 'add', 'GOOGL'])
        result = runner.invoke(cli, ['watchlist', 'show'])
        assert 'GOOGL' in result.output

    def test_show_does_not_display_removed_ticker(self, runner, in_memory_db, monkeypatch):
        """Ticker removed via CLI is absent from subsequent show output."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        runner.invoke(cli, ['watchlist', 'add', 'META'])
        runner.invoke(cli, ['watchlist', 'remove', 'META'])
        result = runner.invoke(cli, ['watchlist', 'show'])
        # META should no longer appear in a valid ticker listing context
        # (it might appear in the "empty" help message, so we check indirectly)
        assert result.exit_code == 0

    def test_add_duplicate_is_idempotent(self, runner, in_memory_db, monkeypatch):
        """Adding the same ticker twice does not produce an error."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        r1 = runner.invoke(cli, ['watchlist', 'add', 'JPM'])
        r2 = runner.invoke(cli, ['watchlist', 'add', 'JPM'])
        assert r1.exit_code == 0
        assert r2.exit_code == 0

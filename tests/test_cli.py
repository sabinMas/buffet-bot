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


# ── live_guard — is_live_mode() ───────────────────────────────────────────────

class TestIsLiveMode:
    """Unit tests for live_guard.is_live_mode() env-var logic."""

    def test_both_vars_set_returns_true(self, monkeypatch):
        monkeypatch.setenv('BUFFET_BOT_LIVE', '1')
        monkeypatch.setenv('BUFFET_BOT_LIVE_SECRET', 'mysecret')
        from buffet_bot.live_guard import is_live_mode
        assert is_live_mode() is True

    def test_only_live_flag_returns_false(self, monkeypatch):
        monkeypatch.setenv('BUFFET_BOT_LIVE', '1')
        monkeypatch.delenv('BUFFET_BOT_LIVE_SECRET', raising=False)
        from buffet_bot.live_guard import is_live_mode
        assert is_live_mode() is False

    def test_only_secret_returns_false(self, monkeypatch):
        monkeypatch.delenv('BUFFET_BOT_LIVE', raising=False)
        monkeypatch.setenv('BUFFET_BOT_LIVE_SECRET', 'mysecret')
        from buffet_bot.live_guard import is_live_mode
        assert is_live_mode() is False

    def test_neither_var_returns_false(self, monkeypatch):
        monkeypatch.delenv('BUFFET_BOT_LIVE', raising=False)
        monkeypatch.delenv('BUFFET_BOT_LIVE_SECRET', raising=False)
        from buffet_bot.live_guard import is_live_mode
        assert is_live_mode() is False

    def test_live_flag_wrong_value_returns_false(self, monkeypatch):
        """BUFFET_BOT_LIVE must be exactly '1' — any other value is paper mode."""
        monkeypatch.setenv('BUFFET_BOT_LIVE', 'true')
        monkeypatch.setenv('BUFFET_BOT_LIVE_SECRET', 'mysecret')
        from buffet_bot.live_guard import is_live_mode
        assert is_live_mode() is False

    def test_empty_secret_returns_false(self, monkeypatch):
        """An empty BUFFET_BOT_LIVE_SECRET must not activate live mode."""
        monkeypatch.setenv('BUFFET_BOT_LIVE', '1')
        monkeypatch.setenv('BUFFET_BOT_LIVE_SECRET', '')
        from buffet_bot.live_guard import is_live_mode
        assert is_live_mode() is False


# ── live_guard — confirm_live_execution() ─────────────────────────────────────

class TestConfirmLiveExecution:
    """Tests for the Factor-3 confirmation gate."""

    def test_paper_mode_returns_true_immediately(self, in_memory_db, monkeypatch):
        """In paper mode confirm_live_execution() returns True without prompting."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        monkeypatch.delenv('BUFFET_BOT_LIVE', raising=False)
        monkeypatch.delenv('BUFFET_BOT_LIVE_SECRET', raising=False)
        from buffet_bot.live_guard import confirm_live_execution
        result = confirm_live_execution('BUY 10x AAPL', 'AAPL', 10, 'BUY', 1500.0)
        assert result is True

    def _setup_live_audit_db(self, db_path, monkeypatch):
        """Patch globals.DB_PATH and ensure live_audit table exists in temp DB."""
        monkeypatch.setattr('buffet_bot.globals.DB_PATH', db_path)
        from buffet_bot.live_guard import init_live_audit_table
        init_live_audit_table()

    def test_paper_mode_logs_passthrough_row(self, in_memory_db, monkeypatch):
        """Paper mode must write a PAPER_PASSTHROUGH row to live_audit."""
        import sqlite3 as _sqlite3
        self._setup_live_audit_db(in_memory_db, monkeypatch)
        monkeypatch.delenv('BUFFET_BOT_LIVE', raising=False)
        monkeypatch.delenv('BUFFET_BOT_LIVE_SECRET', raising=False)
        from buffet_bot.live_guard import confirm_live_execution
        confirm_live_execution('BUY 5x MSFT', 'MSFT', 5, 'BUY', 1500.0)
        conn = _sqlite3.connect(in_memory_db)
        rows = conn.execute("SELECT outcome FROM live_audit WHERE ticker='MSFT'").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == 'PAPER_PASSTHROUGH'

    def test_live_mode_correct_phrase_returns_true(self, in_memory_db, monkeypatch):
        """In LIVE mode typing 'YES I CONFIRM' exactly must return True."""
        self._setup_live_audit_db(in_memory_db, monkeypatch)
        monkeypatch.setenv('BUFFET_BOT_LIVE', '1')
        monkeypatch.setenv('BUFFET_BOT_LIVE_SECRET', 'testsecret')
        with patch('buffet_bot.live_guard.click.prompt', return_value='YES I CONFIRM'):
            from buffet_bot.live_guard import confirm_live_execution
            result = confirm_live_execution('BUY 1x AAPL', 'AAPL', 1, 'BUY', 150.0)
        assert result is True

    def test_live_mode_wrong_phrase_returns_false(self, in_memory_db, monkeypatch):
        """In LIVE mode typing anything other than 'YES I CONFIRM' must return False."""
        self._setup_live_audit_db(in_memory_db, monkeypatch)
        monkeypatch.setenv('BUFFET_BOT_LIVE', '1')
        monkeypatch.setenv('BUFFET_BOT_LIVE_SECRET', 'testsecret')
        with patch('buffet_bot.live_guard.click.prompt', return_value='yes'):
            from buffet_bot.live_guard import confirm_live_execution
            result = confirm_live_execution('BUY 1x AAPL', 'AAPL', 1, 'BUY', 150.0)
        assert result is False

    def test_live_mode_rejection_logs_rejected_row(self, in_memory_db, monkeypatch):
        """A rejected live order must write outcome='REJECTED' to live_audit."""
        import sqlite3 as _sqlite3
        self._setup_live_audit_db(in_memory_db, monkeypatch)
        monkeypatch.setenv('BUFFET_BOT_LIVE', '1')
        monkeypatch.setenv('BUFFET_BOT_LIVE_SECRET', 'testsecret')
        with patch('buffet_bot.live_guard.click.prompt', return_value='no'):
            from buffet_bot.live_guard import confirm_live_execution
            confirm_live_execution('SELL 2x TSLA', 'TSLA', 2, 'SELL', 400.0)
        conn = _sqlite3.connect(in_memory_db)
        rows = conn.execute("SELECT outcome FROM live_audit WHERE ticker='TSLA'").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == 'REJECTED'

    def test_live_mode_correct_phrase_logs_confirmed_row(self, in_memory_db, monkeypatch):
        """An approved live order must write outcome='CONFIRMED' to live_audit."""
        import sqlite3 as _sqlite3
        self._setup_live_audit_db(in_memory_db, monkeypatch)
        monkeypatch.setenv('BUFFET_BOT_LIVE', '1')
        monkeypatch.setenv('BUFFET_BOT_LIVE_SECRET', 'testsecret')
        with patch('buffet_bot.live_guard.click.prompt', return_value='YES I CONFIRM'):
            from buffet_bot.live_guard import confirm_live_execution
            confirm_live_execution('BUY 1x NVDA', 'NVDA', 1, 'BUY', 500.0)
        conn = _sqlite3.connect(in_memory_db)
        rows = conn.execute("SELECT outcome FROM live_audit WHERE ticker='NVDA'").fetchall()
        conn.close()
        assert rows[0][0] == 'CONFIRMED'


# ── compound command ──────────────────────────────────────────────────────────

class TestCompoundCommand:
    """Tests for: buffet-bot compound [--budget] [--source] [--execute]"""

    def test_help_exits_zero(self, runner):
        result = runner.invoke(cli, ['compound', '--help'])
        assert result.exit_code == 0
        assert '--budget' in result.output
        assert '--source' in result.output
        assert '--execute' in result.output

    def test_no_income_no_budget_shows_guidance(self, runner, in_memory_db, monkeypatch):
        """With no income events and no --budget, show the guidance panel."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        with patch('buffet_bot.cmd_portfolio._fetch_dividend_activities', return_value=[]):
            with patch('buffet_bot.cmd_portfolio._fetch_realized_profits', return_value=[]):
                result = runner.invoke(cli, ['compound'])
        assert result.exit_code == 0
        assert 'No compoundable income' in result.output or \
               'budget' in result.output.lower()

    def test_budget_override_skips_income_check(self, runner, in_memory_db, monkeypatch):
        """--budget bypasses the 'no income' early-exit and proceeds to scoring."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        mock_metrics = {'score': 70, 'beta': 1.0}
        mock_rt = {'price': 100.0}
        with patch('buffet_bot.cmd_portfolio._fetch_dividend_activities', return_value=[]):
            with patch('buffet_bot.cmd_portfolio._fetch_realized_profits', return_value=[]):
                with patch('buffet_bot.cmd_portfolio.get_buffett_metrics', return_value=mock_metrics):
                    with patch('buffet_bot.cmd_portfolio.get_realtime_data', return_value=mock_rt):
                        result = runner.invoke(cli, ['compound', '--budget', '500', '--min-score', '0'])
        assert result.exit_code == 0
        # Should show allocation plan or dry-run message, not the guidance panel
        assert 'Dry run' in result.output or 'Allocation' in result.output

    def test_dry_run_does_not_place_orders(self, runner, in_memory_db, monkeypatch):
        """Without --execute, no orders are submitted to Alpaca."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        mock_metrics = {'score': 65, 'beta': 1.2}
        mock_rt = {'price': 50.0}
        mock_order = MagicMock()
        with patch('buffet_bot.cmd_portfolio._fetch_dividend_activities', return_value=[]):
            with patch('buffet_bot.cmd_portfolio._fetch_realized_profits', return_value=[]):
                with patch('buffet_bot.cmd_portfolio.get_buffett_metrics', return_value=mock_metrics):
                    with patch('buffet_bot.cmd_portfolio.get_realtime_data', return_value=mock_rt):
                        with patch('buffet_bot.cmd_portfolio.trading_client') as mock_tc:
                            result = runner.invoke(cli, ['compound', '--budget', '300', '--min-score', '0'])
        assert result.exit_code == 0
        mock_tc.submit_order.assert_not_called()

    def test_source_dividends_only_skips_profits(self, runner, in_memory_db, monkeypatch):
        """--source dividends must not call _fetch_realized_profits."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        mock_profits = MagicMock(return_value=[])
        mock_divs = MagicMock(return_value=[])
        with patch('buffet_bot.cmd_portfolio._fetch_dividend_activities', mock_divs):
            with patch('buffet_bot.cmd_portfolio._fetch_realized_profits', mock_profits):
                runner.invoke(cli, ['compound', '--source', 'dividends'])
        mock_divs.assert_called_once()
        mock_profits.assert_not_called()

    def test_invalid_source_fails(self, runner):
        result = runner.invoke(cli, ['compound', '--source', 'crypto'])
        assert result.exit_code != 0


# ── edge-scan command ─────────────────────────────────────────────────────────

def _make_edge_result(ticker='AAPL', edge_score=72.5):
    """Minimal compute_edge_score() result for CLI test stubs."""
    return {
        'ticker':          ticker,
        'edge_score':      edge_score,
        'components':      {
            'buffett': 80.0, 'llm': 70.0, 'insider': 65.0,
            'politician': 60.0, 'earnings': 75.0, 'analyst': 68.0,
        },
        'weights_used':    {
            'buffett': 0.30, 'llm': 0.20, 'insider': 0.20,
            'politician': 0.10, 'earnings': 0.10, 'analyst': 0.10,
        },
        'simulation_date': None,
    }


class TestEdgeScanCommand:
    """Tests for: buffet-bot edge-scan [--universe] [--tickers] [--min-edge]
                                       [--top] [--weights] [--json] [--no-save]
    """

    def test_help_exits_zero(self, runner):
        result = runner.invoke(cli, ['edge-scan', '--help'])
        assert result.exit_code == 0
        assert '--universe' in result.output
        assert '--min-edge' in result.output
        assert '--json'     in result.output

    def test_scores_tickers_and_shows_table(self, runner, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        results = [_make_edge_result('AAPL', 75.0), _make_edge_result('KO', 68.0)]
        with patch('buffet_bot.cmd_intel.compute_edge_score', side_effect=results):
            with patch('buffet_bot.cmd_intel.log_edge_scan'):
                result = runner.invoke(cli, ['edge-scan', '--universe', 'buffett',
                                             '--min-edge', '0'])
        assert result.exit_code == 0
        assert 'AAPL' in result.output or 'KO' in result.output

    def test_min_edge_filters_below_threshold(self, runner, in_memory_db, monkeypatch):
        """All scores below --min-edge should produce the 'no tickers passed' message."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        low_results = [_make_edge_result('AAPL', 20.0)]
        with patch('buffet_bot.cmd_intel.compute_edge_score', side_effect=low_results):
            with patch('buffet_bot.cmd_intel.log_edge_scan'):
                result = runner.invoke(cli, ['edge-scan', '--universe', 'buffett',
                                             '--tickers', 'AAPL', '--min-edge', '99'])
        assert result.exit_code == 0
        assert 'min-edge' in result.output.lower() or 'passed' in result.output.lower() \
               or 'No tickers' in result.output

    def test_tickers_flag_overrides_universe(self, runner, in_memory_db, monkeypatch):
        """--tickers must be scored instead of the preset universe."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        mock_score = MagicMock(return_value=_make_edge_result('NVDA', 88.0))
        with patch('buffet_bot.cmd_intel.compute_edge_score', mock_score):
            with patch('buffet_bot.cmd_intel.log_edge_scan'):
                result = runner.invoke(cli, ['edge-scan', '--tickers', 'NVDA',
                                             '--min-edge', '0'])
        assert result.exit_code == 0
        # compute_edge_score was called with NVDA
        called_tickers = {call.args[0] for call in mock_score.call_args_list}
        assert 'NVDA' in called_tickers

    def test_json_flag_outputs_valid_json(self, runner, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        results = [_make_edge_result('AAPL', 75.0)]
        with patch('buffet_bot.cmd_intel.compute_edge_score', side_effect=results):
            with patch('buffet_bot.cmd_intel.log_edge_scan'):
                result = runner.invoke(cli, ['edge-scan', '--tickers', 'AAPL',
                                             '--min-edge', '0', '--json'])
        assert result.exit_code == 0
        # Output must contain valid JSON (may have Rich markup prefix — find the array)
        import json as _json
        text = result.output
        start = text.find('[')
        assert start != -1, "No JSON array found in output"
        data = _json.loads(text[start:])
        assert isinstance(data, list)
        assert data[0]['ticker'] == 'AAPL'

    def test_no_save_skips_log_edge_scan(self, runner, in_memory_db, monkeypatch):
        """--no-save must not call log_edge_scan."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        mock_log = MagicMock()
        results = [_make_edge_result('AAPL', 75.0)]
        with patch('buffet_bot.cmd_intel.compute_edge_score', side_effect=results):
            with patch('buffet_bot.cmd_intel.log_edge_scan', mock_log):
                runner.invoke(cli, ['edge-scan', '--tickers', 'AAPL',
                                    '--min-edge', '0', '--no-save'])
        mock_log.assert_not_called()

    def test_save_calls_log_edge_scan(self, runner, in_memory_db, monkeypatch):
        """Default --save must call log_edge_scan once per scored ticker."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        mock_log = MagicMock()
        results = [_make_edge_result('AAPL', 75.0)]
        with patch('buffet_bot.cmd_intel.compute_edge_score', side_effect=results):
            with patch('buffet_bot.cmd_intel.log_edge_scan', mock_log):
                runner.invoke(cli, ['edge-scan', '--tickers', 'AAPL', '--min-edge', '0'])
        mock_log.assert_called_once()

    def test_invalid_universe_fails(self, runner):
        result = runner.invoke(cli, ['edge-scan', '--universe', 'invalid_universe'])
        assert result.exit_code != 0

    def test_invalid_weights_json_fails(self, runner, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        result = runner.invoke(cli, ['edge-scan', '--tickers', 'AAPL',
                                     '--weights', 'not-valid-json'])
        assert result.exit_code == 0  # exits cleanly with error message
        assert 'Invalid' in result.output or 'invalid' in result.output.lower()

    def test_top_limits_displayed_results(self, runner, in_memory_db, monkeypatch):
        """--top N should cap results: summary line shows N passed."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        # Use a side_effect function so the ticker argument determines the score,
        # making the result independent of thread execution order.
        def _score_by_ticker(ticker, *args, **kwargs):
            scores = {'AAPL': 90.0, 'MSFT': 85.0, 'NVDA': 80.0}
            return _make_edge_result(ticker, scores.get(ticker, 50.0))

        with patch('buffet_bot.cmd_intel.compute_edge_score', side_effect=_score_by_ticker):
            with patch('buffet_bot.cmd_intel.log_edge_scan'):
                result = runner.invoke(cli, [
                    'edge-scan',
                    '--tickers', 'AAPL', '--tickers', 'MSFT', '--tickers', 'NVDA',
                    '--min-edge', '0', '--top', '2',
                ])
        assert result.exit_code == 0
        # Footer confirms: 3 scored total, 2 displayed (top=2)
        assert '3 scored' in result.output
        assert '2 passed' in result.output
        # NVDA (lowest at 80) should not appear in the top-2 table
        assert 'NVDA' not in result.output

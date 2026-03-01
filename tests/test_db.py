"""Phase 2: DB layer tests — in-memory SQLite via in_memory_db fixture."""
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta


# ── init_db ───────────────────────────────────────────────────────────────────

class TestInitDb:
    def test_creates_recommendations_table(self, in_memory_db):
        conn = sqlite3.connect(in_memory_db)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert 'recommendations' in tables

    def test_creates_outcomes_table(self, in_memory_db):
        conn = sqlite3.connect(in_memory_db)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert 'outcomes' in tables

    def test_creates_watchlist_table(self, in_memory_db):
        conn = sqlite3.connect(in_memory_db)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert 'watchlist' in tables

    def test_creates_alerts_table(self, in_memory_db):
        conn = sqlite3.connect(in_memory_db)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert 'alerts' in tables

    def test_is_idempotent(self, in_memory_db, monkeypatch):
        """Calling init_db() multiple times must not raise or corrupt the schema."""
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import init_db
        init_db()  # second call
        init_db()  # third call
        # Verify tables still intact after repeated calls
        conn = sqlite3.connect(in_memory_db)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert {'recommendations', 'outcomes', 'watchlist', 'alerts'}.issubset(tables)


# ── log_recommendation ────────────────────────────────────────────────────────

class TestLogRecommendation:
    def test_inserts_a_row(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import log_recommendation, get_recent_recommendations
        log_recommendation('AAPL', 'BUY', 0.85, 10, 150.0, 'Good company', 'deepseek-r1', 'value', 75)
        rows = get_recent_recommendations(1)
        assert len(rows) == 1
        assert rows[0]['ticker'] == 'AAPL'

    def test_stores_correct_values(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import log_recommendation, get_recent_recommendations
        log_recommendation('MSFT', 'BUY', 0.90, 5, 300.0, 'Strong cloud', 'qwen2.5:7b', 'growth', 80)
        rows = get_recent_recommendations(1)
        row = rows[0]
        assert row['ticker'] == 'MSFT'
        assert abs(row['confidence'] - 0.90) < 1e-6
        assert row['qty'] == 5
        assert abs(row['entry_price'] - 300.0) < 1e-6
        assert row['model'] == 'qwen2.5:7b'
        assert row['strategy'] == 'growth'
        assert row['buffett_score'] == 80

    def test_truncates_reason_at_500_chars(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import log_recommendation, get_recent_recommendations
        long_reason = 'X' * 1000
        log_recommendation('AAPL', 'BUY', 0.8, 10, 150.0, long_reason, 'deepseek-r1', 'value', 75)
        rows = get_recent_recommendations(1)
        assert len(rows[0]['reason']) == 500

    def test_silent_on_exception(self, monkeypatch):
        """log_recommendation must not raise even with a bad DB path."""
        monkeypatch.setattr('buffet_bot.main.DB_PATH', '/nonexistent/path/test.db')
        from buffet_bot.main import log_recommendation
        # Must not raise
        log_recommendation('AAPL', 'BUY', 0.8, 10, 150.0, 'ok', 'deepseek-r1', 'value', 75)


# ── get_recent_recommendations ────────────────────────────────────────────────

class TestGetRecentRecommendations:
    def _insert_old_row(self, db_path, days_ago):
        """Helper: insert a recommendation with a backdated timestamp."""
        old_ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO recommendations "
            "(timestamp,ticker,action,confidence,qty,entry_price,reason,model,strategy,buffett_score) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (old_ts, 'AAPL', 'BUY', 0.8, 10, 150.0, 'old', 'deepseek-r1', 'value', 75),
        )
        conn.commit()
        conn.close()

    def test_returns_row_within_window(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import log_recommendation, get_recent_recommendations
        log_recommendation('TSLA', 'BUY', 0.75, 3, 200.0, 'EV growth', 'deepseek-r1', 'growth', 60)
        rows = get_recent_recommendations(7)
        assert len(rows) == 1

    def test_excludes_row_outside_window(self, in_memory_db, monkeypatch):
        """A row inserted 10 days ago must not appear in a 7-day window."""
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        self._insert_old_row(in_memory_db, days_ago=10)
        from buffet_bot.main import get_recent_recommendations
        rows = get_recent_recommendations(7)
        assert len(rows) == 0

    def test_includes_row_within_wider_window(self, in_memory_db, monkeypatch):
        """A row from 10 days ago IS within a 30-day window."""
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        self._insert_old_row(in_memory_db, days_ago=10)
        from buffet_bot.main import get_recent_recommendations
        rows = get_recent_recommendations(30)
        assert len(rows) == 1

    def test_returns_list_of_dicts(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import log_recommendation, get_recent_recommendations
        log_recommendation('AAPL', 'BUY', 0.8, 5, 150.0, 'ok', 'deepseek-r1', 'value', 70)
        rows = get_recent_recommendations(30)
        assert isinstance(rows, list)
        assert isinstance(rows[0], dict)

    def test_empty_db_returns_empty_list(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import get_recent_recommendations
        rows = get_recent_recommendations(30)
        assert rows == []

    def test_returns_empty_list_on_bad_db(self, monkeypatch):
        """Must return [] and not raise when DB is unavailable."""
        monkeypatch.setattr('buffet_bot.main.DB_PATH', '/nonexistent/path/test.db')
        from buffet_bot.main import get_recent_recommendations
        rows = get_recent_recommendations(30)
        assert rows == []


# ── Watchlist helpers ─────────────────────────────────────────────────────────

class TestWatchlist:
    def test_add_and_retrieve_ticker(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import add_to_watchlist, get_watchlist
        add_to_watchlist('AAPL')
        tickers = [row['ticker'] for row in get_watchlist()]
        assert 'AAPL' in tickers

    def test_add_uppercases_ticker(self, in_memory_db, monkeypatch):
        """Lowercase input must be stored as uppercase."""
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import add_to_watchlist, get_watchlist
        add_to_watchlist('tsla')
        tickers = [row['ticker'] for row in get_watchlist()]
        assert 'TSLA' in tickers
        assert 'tsla' not in tickers

    def test_duplicate_add_is_idempotent(self, in_memory_db, monkeypatch):
        """INSERT OR IGNORE: adding the same ticker twice must not duplicate."""
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import add_to_watchlist, get_watchlist
        add_to_watchlist('AAPL')
        add_to_watchlist('AAPL')
        tickers = [row['ticker'] for row in get_watchlist()]
        assert tickers.count('AAPL') == 1

    def test_remove_existing_ticker(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import add_to_watchlist, remove_from_watchlist, get_watchlist
        add_to_watchlist('AAPL')
        remove_from_watchlist('AAPL')
        tickers = [row['ticker'] for row in get_watchlist()]
        assert 'AAPL' not in tickers

    def test_remove_nonexistent_is_silent(self, in_memory_db, monkeypatch):
        """Removing a ticker that was never added must not raise."""
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import remove_from_watchlist
        remove_from_watchlist('TSLA')  # must not raise

    def test_get_watchlist_sorted_alphabetically(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import add_to_watchlist, get_watchlist
        for ticker in ['TSLA', 'AAPL', 'MSFT', 'GOOG']:
            add_to_watchlist(ticker)
        tickers = [row['ticker'] for row in get_watchlist()]
        assert tickers == sorted(tickers)

    def test_get_watchlist_empty_returns_empty_list(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import get_watchlist
        assert get_watchlist() == []

    def test_watchlist_row_has_ticker_and_added_at(self, in_memory_db, monkeypatch):
        """Each row in get_watchlist() must have 'ticker' and 'added_at' keys."""
        monkeypatch.setattr('buffet_bot.main.DB_PATH', in_memory_db)
        from buffet_bot.main import add_to_watchlist, get_watchlist
        add_to_watchlist('NVDA')
        rows = get_watchlist()
        assert 'ticker' in rows[0]
        assert 'added_at' in rows[0]

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
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import init_db
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
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_recommendation, get_recent_recommendations
        log_recommendation('AAPL', 'BUY', 0.85, 10, 150.0, 'Good company', 'deepseek-r1', 'value', 75)
        rows = get_recent_recommendations(1)
        assert len(rows) == 1
        assert rows[0]['ticker'] == 'AAPL'

    def test_stores_correct_values(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_recommendation, get_recent_recommendations
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
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_recommendation, get_recent_recommendations
        long_reason = 'X' * 1000
        log_recommendation('AAPL', 'BUY', 0.8, 10, 150.0, long_reason, 'deepseek-r1', 'value', 75)
        rows = get_recent_recommendations(1)
        assert len(rows[0]['reason']) == 500

    def test_silent_on_exception(self, monkeypatch):
        """log_recommendation must not raise even with a bad DB path."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', '/nonexistent/path/test.db')
        from buffet_bot.db import log_recommendation
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
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_recommendation, get_recent_recommendations
        log_recommendation('TSLA', 'BUY', 0.75, 3, 200.0, 'EV growth', 'deepseek-r1', 'growth', 60)
        rows = get_recent_recommendations(7)
        assert len(rows) == 1

    def test_excludes_row_outside_window(self, in_memory_db, monkeypatch):
        """A row inserted 10 days ago must not appear in a 7-day window."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        self._insert_old_row(in_memory_db, days_ago=10)
        from buffet_bot.db import get_recent_recommendations
        rows = get_recent_recommendations(7)
        assert len(rows) == 0

    def test_includes_row_within_wider_window(self, in_memory_db, monkeypatch):
        """A row from 10 days ago IS within a 30-day window."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        self._insert_old_row(in_memory_db, days_ago=10)
        from buffet_bot.db import get_recent_recommendations
        rows = get_recent_recommendations(30)
        assert len(rows) == 1

    def test_returns_list_of_dicts(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_recommendation, get_recent_recommendations
        log_recommendation('AAPL', 'BUY', 0.8, 5, 150.0, 'ok', 'deepseek-r1', 'value', 70)
        rows = get_recent_recommendations(30)
        assert isinstance(rows, list)
        assert isinstance(rows[0], dict)

    def test_empty_db_returns_empty_list(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import get_recent_recommendations
        rows = get_recent_recommendations(30)
        assert rows == []

    def test_returns_empty_list_on_bad_db(self, monkeypatch):
        """Must return [] and not raise when DB is unavailable."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', '/nonexistent/path/test.db')
        from buffet_bot.db import get_recent_recommendations
        rows = get_recent_recommendations(30)
        assert rows == []


# ── Watchlist helpers ─────────────────────────────────────────────────────────

class TestWatchlist:
    def test_add_and_retrieve_ticker(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import add_to_watchlist, get_watchlist
        add_to_watchlist('AAPL')
        tickers = [row['ticker'] for row in get_watchlist()]
        assert 'AAPL' in tickers

    def test_add_uppercases_ticker(self, in_memory_db, monkeypatch):
        """Lowercase input must be stored as uppercase."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import add_to_watchlist, get_watchlist
        add_to_watchlist('tsla')
        tickers = [row['ticker'] for row in get_watchlist()]
        assert 'TSLA' in tickers
        assert 'tsla' not in tickers

    def test_duplicate_add_is_idempotent(self, in_memory_db, monkeypatch):
        """INSERT OR IGNORE: adding the same ticker twice must not duplicate."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import add_to_watchlist, get_watchlist
        add_to_watchlist('AAPL')
        add_to_watchlist('AAPL')
        tickers = [row['ticker'] for row in get_watchlist()]
        assert tickers.count('AAPL') == 1

    def test_remove_existing_ticker(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import add_to_watchlist, remove_from_watchlist, get_watchlist
        add_to_watchlist('AAPL')
        remove_from_watchlist('AAPL')
        tickers = [row['ticker'] for row in get_watchlist()]
        assert 'AAPL' not in tickers

    def test_remove_nonexistent_is_silent(self, in_memory_db, monkeypatch):
        """Removing a ticker that was never added must not raise."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import remove_from_watchlist
        remove_from_watchlist('TSLA')  # must not raise

    def test_get_watchlist_sorted_alphabetically(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import add_to_watchlist, get_watchlist
        for ticker in ['TSLA', 'AAPL', 'MSFT', 'GOOG']:
            add_to_watchlist(ticker)
        tickers = [row['ticker'] for row in get_watchlist()]
        assert tickers == sorted(tickers)

    def test_get_watchlist_empty_returns_empty_list(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import get_watchlist
        assert get_watchlist() == []

    def test_watchlist_row_has_ticker_and_added_at(self, in_memory_db, monkeypatch):
        """Each row in get_watchlist() must have 'ticker' and 'added_at' keys."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import add_to_watchlist, get_watchlist
        add_to_watchlist('NVDA')
        rows = get_watchlist()
        assert 'ticker' in rows[0]
        assert 'added_at' in rows[0]


# ── compound_log helpers ──────────────────────────────────────────────────────

class TestCompoundLog:
    """Tests for log_compound_event() and get_compound_history()."""

    def test_creates_compound_log_table(self, in_memory_db):
        conn = sqlite3.connect(in_memory_db)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert 'compound_log' in tables

    def test_log_event_returns_positive_id(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_compound_event
        row_id = log_compound_event(
            source='DIVIDEND', ticker='AAPL', amount_usd=50.0,
            allocated_to=[{'ticker': 'KO', 'qty': 1, 'price': 60.0}],
        )
        assert row_id > 0

    def test_log_event_calculates_total_deployed(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_compound_event, get_compound_history
        log_compound_event(
            source='DIVIDEND', ticker='AAPL', amount_usd=200.0,
            allocated_to=[
                {'ticker': 'KO',  'qty': 2, 'price': 60.0},
                {'ticker': 'JNJ', 'qty': 1, 'price': 50.0},
            ],
        )
        rows = get_compound_history(days=7)
        assert len(rows) == 1
        assert abs(rows[0]['total_deployed'] - 170.0) < 0.01  # 2*60 + 1*50
        assert abs(rows[0]['undeployed']    -  30.0) < 0.01  # 200 - 170

    def test_log_event_stores_source_uppercased(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_compound_event, get_compound_history
        log_compound_event('dividend', 'AAPL', 50.0, [])
        rows = get_compound_history()
        assert rows[0]['source'] == 'DIVIDEND'

    def test_log_event_allocated_to_is_deserialized_list(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_compound_event, get_compound_history
        allocation = [{'ticker': 'V', 'qty': 3, 'price': 250.0}]
        log_compound_event('MANUAL', 'CASH', 750.0, allocation)
        rows = get_compound_history()
        assert isinstance(rows[0]['allocated_to'], list)
        assert rows[0]['allocated_to'][0]['ticker'] == 'V'

    def test_get_compound_history_respects_days_window(self, in_memory_db, monkeypatch):
        """A row older than the window must not be returned."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import get_compound_history
        # Insert a backdated row directly
        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        conn = sqlite3.connect(in_memory_db)
        conn.execute(
            "INSERT INTO compound_log (timestamp, source, ticker, amount_usd, allocated_to) "
            "VALUES (?, 'DIVIDEND', 'AAPL', 100.0, '[]')",
            (old_ts,),
        )
        conn.commit()
        conn.close()
        rows = get_compound_history(days=30)
        assert len(rows) == 0

    def test_get_compound_history_empty_returns_empty_list(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import get_compound_history
        assert get_compound_history() == []

    def test_get_compound_history_bad_db_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', '/nonexistent/path/test.db')
        from buffet_bot.db import get_compound_history
        assert get_compound_history() == []

    def test_log_event_silent_on_bad_db(self, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', '/nonexistent/path/test.db')
        from buffet_bot.db import log_compound_event
        result = log_compound_event('DIVIDEND', 'AAPL', 50.0, [])
        assert result == 0  # returns 0, does not raise


# ── sweeps helpers ────────────────────────────────────────────────────────────

class TestSweeps:
    """Tests for create_sweep(), complete_sweep(), get_sweep_history()."""

    def test_creates_sweeps_table(self, in_memory_db):
        conn = sqlite3.connect(in_memory_db)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert 'sweeps' in tables

    def test_create_sweep_returns_positive_id(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import create_sweep
        sweep_id = create_sweep(goal='invest $500 in value stocks', budget_usd=500.0)
        assert sweep_id > 0

    def test_create_sweep_status_is_running(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import create_sweep
        sweep_id = create_sweep(goal='test', budget_usd=100.0)
        conn = sqlite3.connect(in_memory_db)
        row = conn.execute("SELECT status FROM sweeps WHERE id=?", (sweep_id,)).fetchone()
        conn.close()
        assert row[0] == 'RUNNING'

    def test_complete_sweep_updates_status(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import create_sweep, complete_sweep
        sweep_id = create_sweep(goal='test', budget_usd=100.0)
        complete_sweep(sweep_id, tickers_scanned=10, orders_placed=2,
                       total_deployed=180.0, summary='Bought KO and JNJ')
        conn = sqlite3.connect(in_memory_db)
        row = conn.execute(
            "SELECT status, tickers_scanned, orders_placed, total_deployed, summary "
            "FROM sweeps WHERE id=?", (sweep_id,)
        ).fetchone()
        conn.close()
        assert row[0] == 'COMPLETE'
        assert row[1] == 10
        assert row[2] == 2
        assert abs(row[3] - 180.0) < 0.01
        assert 'KO' in row[4]

    def test_complete_sweep_can_mark_failed(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import create_sweep, complete_sweep
        sweep_id = create_sweep(goal='test', budget_usd=100.0)
        complete_sweep(sweep_id, tickers_scanned=3, orders_placed=0,
                       total_deployed=0.0, summary='timeout', status='FAILED')
        conn = sqlite3.connect(in_memory_db)
        row = conn.execute("SELECT status FROM sweeps WHERE id=?", (sweep_id,)).fetchone()
        conn.close()
        assert row[0] == 'FAILED'

    def test_get_sweep_history_returns_newest_first(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import create_sweep, get_sweep_history
        id1 = create_sweep(goal='first', budget_usd=100.0)
        id2 = create_sweep(goal='second', budget_usd=200.0)
        rows = get_sweep_history()
        assert rows[0]['id'] == id2  # newest first
        assert rows[1]['id'] == id1

    def test_get_sweep_history_respects_limit(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import create_sweep, get_sweep_history
        for i in range(5):
            create_sweep(goal=f'sweep {i}', budget_usd=100.0)
        rows = get_sweep_history(limit=3)
        assert len(rows) == 3

    def test_get_sweep_history_empty_returns_empty_list(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import get_sweep_history
        assert get_sweep_history() == []

    def test_create_sweep_silent_on_bad_db(self, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', '/nonexistent/path/test.db')
        from buffet_bot.db import create_sweep
        result = create_sweep(goal='test', budget_usd=100.0)
        assert result == 0  # returns 0, does not raise

    def test_complete_sweep_silent_on_bad_db(self, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', '/nonexistent/path/test.db')
        from buffet_bot.db import complete_sweep
        complete_sweep(999, 0, 0, 0.0, 'summary')  # must not raise


# ── Edge scans ────────────────────────────────────────────────────────────────

def _make_edge_result(ticker='AAPL', edge_score=72.5):
    """Minimal compute_edge_score() result dict for DB helper tests."""
    return {
        'ticker':          ticker,
        'edge_score':      edge_score,
        'components':      {
            'buffett':    80.0,
            'llm':        70.0,
            'insider':    65.0,
            'politician': 60.0,
            'earnings':   75.0,
            'analyst':    68.0,
        },
        'weights_used':    {
            'buffett': 0.30, 'llm': 0.20, 'insider': 0.20,
            'politician': 0.10, 'earnings': 0.10, 'analyst': 0.10,
        },
        'simulation_date': None,
    }


class TestEdgeTable:
    """Tests for init_edge_table(), log_edge_scan(), get_edge_history()."""

    def test_creates_edge_scans_table(self, in_memory_db):
        conn = sqlite3.connect(in_memory_db)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert 'edge_scans' in tables

    def test_log_edge_scan_returns_positive_id(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_edge_scan
        row_id = log_edge_scan(_make_edge_result())
        assert row_id > 0

    def test_log_edge_scan_stores_ticker_and_score(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_edge_scan
        row_id = log_edge_scan(_make_edge_result('MSFT', 65.3))
        conn = sqlite3.connect(in_memory_db)
        row = conn.execute(
            "SELECT ticker, edge_score FROM edge_scans WHERE id=?", (row_id,)
        ).fetchone()
        conn.close()
        assert row[0] == 'MSFT'
        assert abs(row[1] - 65.3) < 0.01

    def test_log_edge_scan_stores_component_scores(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_edge_scan
        result = _make_edge_result()
        row_id = log_edge_scan(result)
        conn = sqlite3.connect(in_memory_db)
        row = conn.execute(
            "SELECT buffett_score, insider_score, politician_score, "
            "       earnings_score, analyst_score "
            "FROM edge_scans WHERE id=?", (row_id,)
        ).fetchone()
        conn.close()
        assert abs(row[0] - 80.0) < 0.01   # buffett
        assert abs(row[1] - 65.0) < 0.01   # insider
        assert abs(row[2] - 60.0) < 0.01   # politician
        assert abs(row[3] - 75.0) < 0.01   # earnings
        assert abs(row[4] - 68.0) < 0.01   # analyst

    def test_get_edge_history_returns_rows(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_edge_scan, get_edge_history
        log_edge_scan(_make_edge_result('AAPL', 70.0))
        log_edge_scan(_make_edge_result('MSFT', 80.0))
        rows = get_edge_history()
        assert len(rows) == 2

    def test_get_edge_history_filters_by_ticker(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_edge_scan, get_edge_history
        log_edge_scan(_make_edge_result('AAPL', 70.0))
        log_edge_scan(_make_edge_result('MSFT', 80.0))
        rows = get_edge_history(ticker='AAPL')
        assert len(rows) == 1
        assert rows[0]['ticker'] == 'AAPL'

    def test_get_edge_history_respects_limit(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import log_edge_scan, get_edge_history
        for i in range(5):
            log_edge_scan(_make_edge_result('AAPL', float(i * 10)))
        rows = get_edge_history(ticker='AAPL', limit=3)
        assert len(rows) == 3

    def test_get_edge_history_empty_returns_empty_list(self, in_memory_db, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import get_edge_history
        assert get_edge_history() == []

    def test_log_edge_scan_silent_on_bad_db(self, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', '/nonexistent/path/test.db')
        from buffet_bot.db import log_edge_scan
        result = log_edge_scan(_make_edge_result())
        assert result == 0  # returns 0, does not raise

    def test_get_edge_history_silent_on_bad_db(self, monkeypatch):
        monkeypatch.setattr('buffet_bot.db.DB_PATH', '/nonexistent/path/test.db')
        from buffet_bot.db import get_edge_history
        assert get_edge_history() == []


# ── get_earnings_history before_date filter (anti-lookahead bias) ─────────────

class TestEarningsHistoryBeforeDate:
    """Tests for get_earnings_history() before_date parameter (anti-lookahead)."""

    def _seed_earnings(self, db_path, ticker='AAPL', rows=None):
        """Insert earnings rows directly into the test DB."""
        import sqlite3
        conn = sqlite3.connect(db_path)
        for r in (rows or []):
            conn.execute(
                """INSERT OR IGNORE INTO earnings_surprises
                   (ticker, report_date, eps_actual, eps_forecast,
                    surprise_pct, beat_miss, logged_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (ticker, r['date'], r['eps_a'], r['eps_f'], r['surprise'],
                 r['beat_miss'], '2026-01-01T00:00:00+00:00'),
            )
        conn.commit()
        conn.close()

    def test_before_date_excludes_future_rows(self, in_memory_db, monkeypatch):
        """Rows with report_date >= before_date must not be returned."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import get_earnings_history
        self._seed_earnings(in_memory_db, rows=[
            {'date': '2024-01-01', 'eps_a': 2.0, 'eps_f': 1.5, 'surprise': 33.3, 'beat_miss': 'BEAT'},
            {'date': '2025-06-01', 'eps_a': 1.0, 'eps_f': 1.5, 'surprise': -33.3, 'beat_miss': 'MISS'},
        ])
        rows = get_earnings_history('AAPL', before_date='2025-01-01')
        assert len(rows) == 1
        assert rows[0]['report_date'] == '2024-01-01'

    def test_before_date_none_returns_all_rows(self, in_memory_db, monkeypatch):
        """Without before_date, all rows are returned (existing behaviour unchanged)."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import get_earnings_history
        self._seed_earnings(in_memory_db, rows=[
            {'date': '2024-01-01', 'eps_a': 2.0, 'eps_f': 1.5, 'surprise': 33.3, 'beat_miss': 'BEAT'},
            {'date': '2025-06-01', 'eps_a': 1.0, 'eps_f': 1.5, 'surprise': -33.3, 'beat_miss': 'MISS'},
        ])
        rows = get_earnings_history('AAPL', before_date=None)
        assert len(rows) == 2

    def test_before_date_all_rows_filtered_returns_empty(self, in_memory_db, monkeypatch):
        """If before_date is before all rows, result is empty (no crash)."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import get_earnings_history
        self._seed_earnings(in_memory_db, rows=[
            {'date': '2026-01-01', 'eps_a': 2.0, 'eps_f': 1.5, 'surprise': 33.3, 'beat_miss': 'BEAT'},
        ])
        rows = get_earnings_history('AAPL', before_date='2020-01-01')
        assert rows == []

    def test_before_date_no_ticker_filter(self, in_memory_db, monkeypatch):
        """before_date works when ticker='' (all-ticker query path)."""
        monkeypatch.setattr('buffet_bot.db.DB_PATH', in_memory_db)
        from buffet_bot.db import get_earnings_history
        self._seed_earnings(in_memory_db, 'AAPL', rows=[
            {'date': '2024-06-01', 'eps_a': 2.0, 'eps_f': 1.5, 'surprise': 33.3, 'beat_miss': 'BEAT'},
        ])
        self._seed_earnings(in_memory_db, 'MSFT', rows=[
            {'date': '2025-06-01', 'eps_a': 2.0, 'eps_f': 1.5, 'surprise': 33.3, 'beat_miss': 'BEAT'},
        ])
        rows = get_earnings_history('', before_date='2025-01-01')
        tickers = {r['ticker'] for r in rows}
        assert 'AAPL' in tickers
        assert 'MSFT' not in tickers

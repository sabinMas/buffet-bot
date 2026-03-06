"""Database layer — SQLite persistence for buffet-bot."""
import sqlite3
from datetime import datetime, timezone, timedelta

from buffet_bot.globals import DB_PATH


def init_compound_tables() -> None:
    """Create compound_log and sweeps tables if they don't exist."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS compound_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT    NOT NULL,
                source          TEXT    NOT NULL,
                ticker          TEXT    NOT NULL,
                amount_usd      REAL    NOT NULL,
                allocated_to    TEXT    NOT NULL,
                total_deployed  REAL    NOT NULL DEFAULT 0.0,
                undeployed      REAL    NOT NULL DEFAULT 0.0,
                notes           TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_compound_log_timestamp
                ON compound_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_compound_log_source
                ON compound_log(source);

            CREATE TABLE IF NOT EXISTS sweeps (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at      TEXT    NOT NULL,
                completed_at    TEXT    NOT NULL DEFAULT '',
                goal            TEXT    NOT NULL,
                budget_usd      REAL    NOT NULL,
                tickers_scanned INTEGER NOT NULL DEFAULT 0,
                orders_placed   INTEGER NOT NULL DEFAULT 0,
                total_deployed  REAL    NOT NULL DEFAULT 0.0,
                summary         TEXT    NOT NULL DEFAULT '',
                status          TEXT    NOT NULL DEFAULT 'RUNNING'
            );
        """)
        conn.commit()
        conn.close()
    except Exception:
        pass


def log_compound_event(source: str, ticker: str, amount_usd: float,
                       allocated_to: list) -> int:
    """Insert a compound_log row. Returns new row id or 0 on error."""
    import json as _json
    try:
        total_deployed = sum(a.get('qty', 0) * a.get('price', 0) for a in allocated_to)
        undeployed = max(0.0, amount_usd - total_deployed)
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            """INSERT INTO compound_log
               (timestamp, source, ticker, amount_usd, allocated_to,
                total_deployed, undeployed)
               VALUES (?,?,?,?,?,?,?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                source.upper(),
                ticker.upper(),
                round(float(amount_usd), 2),
                _json.dumps(allocated_to),
                round(float(total_deployed), 2),
                round(float(undeployed), 2),
            ),
        )
        row_id = cur.lastrowid
        conn.commit()
        conn.close()
        return row_id or 0
    except Exception:
        return 0


def get_compound_history(days: int = 90) -> list[dict]:
    """Return compound_log rows within the last N days, newest first."""
    import json as _json
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            """SELECT id, timestamp, source, ticker, amount_usd,
                      allocated_to, total_deployed, undeployed, notes
               FROM compound_log WHERE timestamp >= ?
               ORDER BY timestamp DESC""",
            (cutoff,),
        ).fetchall()
        conn.close()
        cols = ['id', 'timestamp', 'source', 'ticker', 'amount_usd',
                'allocated_to', 'total_deployed', 'undeployed', 'notes']
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            try:
                d['allocated_to'] = _json.loads(d['allocated_to'])
            except Exception:
                d['allocated_to'] = []
            result.append(d)
        return result
    except Exception:
        return []


def create_sweep(goal: str, budget_usd: float) -> int:
    """Insert a sweeps row with status=RUNNING. Returns new row id or 0."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            "INSERT INTO sweeps (started_at, goal, budget_usd, status) VALUES (?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), goal,
             round(float(budget_usd), 2), 'RUNNING'),
        )
        row_id = cur.lastrowid
        conn.commit()
        conn.close()
        return row_id or 0
    except Exception:
        return 0


def complete_sweep(sweep_id: int, tickers_scanned: int, orders_placed: int,
                   total_deployed: float, summary: str,
                   status: str = 'COMPLETE') -> None:
    """Update a sweeps row to COMPLETE or FAILED."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """UPDATE sweeps
               SET completed_at=?, tickers_scanned=?, orders_placed=?,
                   total_deployed=?, summary=?, status=?
               WHERE id=?""",
            (
                datetime.now(timezone.utc).isoformat(),
                int(tickers_scanned),
                int(orders_placed),
                round(float(total_deployed), 2),
                str(summary)[:1000],
                status.upper(),
                int(sweep_id),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_sweep_history(limit: int = 20) -> list[dict]:
    """Return recent sweep rows, newest first."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            """SELECT id, started_at, completed_at, goal, budget_usd,
                      tickers_scanned, orders_placed, total_deployed, summary, status
               FROM sweeps ORDER BY started_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        cols = ['id', 'started_at', 'completed_at', 'goal', 'budget_usd',
                'tickers_scanned', 'orders_placed', 'total_deployed', 'summary', 'status']
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []


def init_db():
    """Create recommendation/outcome tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT    NOT NULL,
            ticker        TEXT    NOT NULL,
            action        TEXT    NOT NULL,
            confidence    REAL    NOT NULL DEFAULT 0.0,
            qty           INTEGER NOT NULL DEFAULT 0,
            entry_price   REAL    NOT NULL DEFAULT 0.0,
            reason        TEXT    NOT NULL DEFAULT '',
            model         TEXT    NOT NULL DEFAULT '',
            strategy      TEXT    NOT NULL DEFAULT 'value',
            buffett_score INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS outcomes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER NOT NULL REFERENCES recommendations(id),
            exit_timestamp    TEXT    NOT NULL,
            exit_price        REAL    NOT NULL DEFAULT 0.0,
            pnl_pct           REAL    NOT NULL DEFAULT 0.0,
            holding_days      INTEGER NOT NULL DEFAULT 0,
            outcome_note      TEXT    NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS watchlist (
            ticker   TEXT PRIMARY KEY,
            added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker     TEXT    NOT NULL,
            type       TEXT    NOT NULL,
            threshold  REAL    NOT NULL,
            note       TEXT    NOT NULL DEFAULT '',
            created_at TEXT    NOT NULL,
            triggered  INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS earnings_surprises (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker        TEXT    NOT NULL,
            report_date   TEXT    NOT NULL,
            eps_actual    REAL    NOT NULL,
            eps_forecast  REAL    NOT NULL,
            surprise_pct  REAL    NOT NULL,
            beat_miss     TEXT    NOT NULL,
            logged_at     TEXT    NOT NULL,
            UNIQUE(ticker, report_date)
        );
    """)
    conn.commit()
    conn.close()
    from buffet_bot.live_guard import init_live_audit_table
    init_live_audit_table()
    init_compound_tables()
    init_edge_table()


def init_edge_table() -> None:
    """Create edge_scans table if it doesn't exist."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS edge_scans (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at      TEXT    NOT NULL,
                ticker          TEXT    NOT NULL,
                edge_score      REAL    NOT NULL,
                buffett_score   REAL    NOT NULL DEFAULT 50.0,
                llm_score       REAL    NOT NULL DEFAULT 50.0,
                insider_score   REAL    NOT NULL DEFAULT 50.0,
                politician_score REAL   NOT NULL DEFAULT 50.0,
                earnings_score  REAL    NOT NULL DEFAULT 50.0,
                analyst_score   REAL    NOT NULL DEFAULT 50.0,
                weights_json    TEXT    NOT NULL DEFAULT '{}',
                simulation_date TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_edge_scans_ticker
                ON edge_scans(ticker);
            CREATE INDEX IF NOT EXISTS idx_edge_scans_scanned_at
                ON edge_scans(scanned_at);
        """)
        conn.commit()
        conn.close()
    except Exception:
        pass


def log_edge_scan(result: dict) -> int:
    """Insert an edge_scans row from a compute_edge_score() result dict.
    Returns new row id or 0 on error.
    """
    import json as _json
    try:
        c = result.get('components', {})
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            """INSERT INTO edge_scans
               (scanned_at, ticker, edge_score,
                buffett_score, llm_score, insider_score,
                politician_score, earnings_score, analyst_score,
                weights_json, simulation_date)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                result['ticker'],
                result['edge_score'],
                c.get('buffett',    50.0),
                c.get('llm',        50.0),
                c.get('insider',    50.0),
                c.get('politician', 50.0),
                c.get('earnings',   50.0),
                c.get('analyst',    50.0),
                _json.dumps(result.get('weights_used', {})),
                result.get('simulation_date') or '',
            ),
        )
        row_id = cur.lastrowid
        conn.commit()
        conn.close()
        return row_id or 0
    except Exception:
        return 0


def get_edge_history(ticker: str = '', days: int = 30, limit: int = 50) -> list[dict]:
    """Return edge_scans rows. Filter by ticker and/or recency window."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(DB_PATH)
        if ticker:
            rows = conn.execute(
                """SELECT ticker, scanned_at, edge_score,
                          buffett_score, llm_score, insider_score,
                          politician_score, earnings_score, analyst_score
                   FROM edge_scans
                   WHERE ticker = ? AND scanned_at >= ?
                   ORDER BY scanned_at DESC LIMIT ?""",
                (ticker.upper(), cutoff, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT ticker, scanned_at, edge_score,
                          buffett_score, llm_score, insider_score,
                          politician_score, earnings_score, analyst_score
                   FROM edge_scans
                   WHERE scanned_at >= ?
                   ORDER BY scanned_at DESC LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
        conn.close()
        cols = ['ticker', 'scanned_at', 'edge_score', 'buffett_score',
                'llm_score', 'insider_score', 'politician_score',
                'earnings_score', 'analyst_score']
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []


def log_recommendation(ticker, action, confidence, qty, entry_price,
                       reason, model, strategy, buffett_score):
    """Insert a BUY recommendation row. Silent on any error."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT INTO recommendations
               (timestamp, ticker, action, confidence, qty, entry_price,
                reason, model, strategy, buffett_score)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                ticker, action,
                float(confidence), int(qty), float(entry_price),
                str(reason)[:500],
                model, strategy, int(buffett_score),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_recent_recommendations(days=30):
    """Return list of dicts for recommendations within the last N days."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT * FROM recommendations WHERE timestamp >= ? ORDER BY timestamp DESC",
            (cutoff,),
        ).fetchall()
        cols = [d[0] for d in conn.execute(
            "SELECT * FROM recommendations LIMIT 0"
        ).description]
        conn.close()
        return [dict(zip(cols, row)) for row in rows]
    except Exception:
        return []


def add_to_watchlist(ticker):
    """Add a ticker to the persistent watchlist. Silent if already present."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (ticker, added_at) VALUES (?, ?)",
            (ticker.upper(), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def remove_from_watchlist(ticker):
    """Remove a ticker from the persistent watchlist."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker.upper(),))
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_watchlist():
    """Return list of tickers in the watchlist, sorted alphabetically."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT ticker, added_at FROM watchlist ORDER BY ticker"
        ).fetchall()
        conn.close()
        return [{'ticker': r[0], 'added_at': r[1][:10]} for r in rows]
    except Exception:
        return []


def create_alert(ticker, alert_type, threshold, note=''):
    """Insert an alert row. Returns the new row id, or None on error."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            "INSERT INTO alerts (ticker, type, threshold, note, created_at) VALUES (?,?,?,?,?)",
            (ticker.upper(), alert_type, float(threshold), note,
             datetime.now(timezone.utc).isoformat()),
        )
        row_id = cur.lastrowid
        conn.commit()
        conn.close()
        return row_id
    except Exception:
        return None


def get_alerts(triggered=False):
    """Return list of alert dicts. triggered=False returns only active alerts."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT id, ticker, type, threshold, note, created_at, triggered "
            "FROM alerts WHERE triggered = ? ORDER BY ticker, type",
            (1 if triggered else 0,),
        ).fetchall()
        conn.close()
        cols = ['id', 'ticker', 'type', 'threshold', 'note', 'created_at', 'triggered']
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []


def delete_alert(alert_id):
    """Delete an alert by id."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM alerts WHERE id = ?", (int(alert_id),))
        conn.commit()
        conn.close()
    except Exception:
        pass


def mark_alert_triggered(alert_id):
    """Mark an alert as triggered (won't appear in future checks)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE alerts SET triggered = 1 WHERE id = ?", (int(alert_id),))
        conn.commit()
        conn.close()
    except Exception:
        pass


def log_earnings_result(ticker: str, report_date: str,
                        eps_actual: float, eps_forecast: float) -> bool:
    """Record an earnings result. Returns True on insert, False on duplicate/error."""
    try:
        surprise_pct = ((eps_actual - eps_forecast) / abs(eps_forecast) * 100
                        if eps_forecast != 0 else 0.0)
        if surprise_pct >= 3:
            beat_miss = 'BEAT'
        elif surprise_pct <= -3:
            beat_miss = 'MISS'
        else:
            beat_miss = 'IN-LINE'
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT OR IGNORE INTO earnings_surprises
               (ticker, report_date, eps_actual, eps_forecast,
                surprise_pct, beat_miss, logged_at)
               VALUES (?,?,?,?,?,?,?)""",
            (ticker.upper(), report_date,
             round(float(eps_actual), 4), round(float(eps_forecast), 4),
             round(surprise_pct, 2), beat_miss,
             datetime.now(timezone.utc).isoformat()),
        )
        inserted = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        conn.close()
        return bool(inserted)
    except Exception:
        return False


def get_earnings_history(ticker: str = '', limit: int = 20) -> list[dict]:
    """Return recent earnings_surprises rows. Pass ticker='' for all tickers."""
    try:
        conn = sqlite3.connect(DB_PATH)
        if ticker:
            rows = conn.execute(
                """SELECT ticker, report_date, eps_actual, eps_forecast,
                          surprise_pct, beat_miss
                   FROM earnings_surprises
                   WHERE ticker = ?
                   ORDER BY report_date DESC LIMIT ?""",
                (ticker.upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT ticker, report_date, eps_actual, eps_forecast,
                          surprise_pct, beat_miss
                   FROM earnings_surprises
                   ORDER BY report_date DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        conn.close()
        cols = ['ticker', 'report_date', 'eps_actual', 'eps_forecast',
                'surprise_pct', 'beat_miss']
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []


init_db()

import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATABASE_NAME = DATA_DIR / "database.db"


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def create_tables():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            first_name TEXT,
            premium INTEGER DEFAULT 0,
            premium_tier TEXT DEFAULT 'FREE',
            premium_until TEXT,
            notifications_enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_telegram_id INTEGER,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'WATCHING',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            triggered_at TEXT,
            activated_at TEXT,
            expires_at TEXT,
            invalidated_at TEXT,
            closed_at TEXT,
            entry REAL NOT NULL,
            preferred_entry_low REAL,
            preferred_entry_high REAL,
            stop REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            tp3 REAL NOT NULL,
            rr REAL NOT NULL,
            confidence REAL NOT NULL,
            bull_score REAL NOT NULL,
            bear_score REAL NOT NULL,
            recommendation TEXT NOT NULL,
            setup_key TEXT NOT NULL,
            features_json TEXT NOT NULL,
            reasons_json TEXT NOT NULL,
            current_price REAL,
            max_profit_pct REAL DEFAULT 0,
            max_drawdown_pct REAL DEFAULT 0,
            tp1_hit_at TEXT,
            tp2_hit_at TEXT,
            tp3_hit_at TEXT,
            stop_hit_at TEXT,
            last_notified_status TEXT,
            notification_chat_id INTEGER,
            monitoring_worker_id TEXT,
            monitoring_lock_until TEXT,
            last_checked_at TEXT,
            next_check_at TEXT,
            consecutive_errors INTEGER DEFAULT 0,
            last_monitor_error TEXT
        )
    """)

    # Safe migrations for databases created by earlier stages.
    for name, definition in {
        "premium_tier": "TEXT DEFAULT 'FREE'",
        "premium_until": "TEXT",
        "notifications_enabled": "INTEGER DEFAULT 1",
    }.items():
        _add_column(conn, "users", name, definition)

    for name, definition in {
        "owner_telegram_id": "INTEGER",
        "triggered_at": "TEXT",
        "activated_at": "TEXT",
        "expires_at": "TEXT",
        "invalidated_at": "TEXT",
        "preferred_entry_low": "REAL",
        "preferred_entry_high": "REAL",
        "last_notified_status": "TEXT",
        "notification_chat_id": "INTEGER",
        "monitoring_worker_id": "TEXT",
        "monitoring_lock_until": "TEXT",
        "last_checked_at": "TEXT",
        "next_check_at": "TEXT",
        "consecutive_errors": "INTEGER DEFAULT 0",
        "last_monitor_error": "TEXT",
    }.items():
        _add_column(conn, "signals", name, definition)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signal_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            price REAL,
            details_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(signal_id) REFERENCES signals(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            payload TEXT NOT NULL,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            telegram_payment_charge_id TEXT,
            provider_payment_charge_id TEXT,
            created_at TEXT NOT NULL
        )
    """)


    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitor_runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id TEXT NOT NULL,
            checked_count INTEGER NOT NULL DEFAULT 0,
            succeeded_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_setup ON signals(setup_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_owner ON signals(owner_telegram_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signal_events_signal ON signal_events(signal_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_monitor_due ON signals(status,next_check_at,monitoring_lock_until)")
    conn.commit()
    conn.close()


def add_user(telegram_id, username, first_name):
    with connect() as conn:
        conn.execute("""
            INSERT INTO users(telegram_id, username, first_name)
            VALUES(?,?,?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
        """, (telegram_id, username, first_name))

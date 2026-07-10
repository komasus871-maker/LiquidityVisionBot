import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATABASE_NAME = DATA_DIR / "database.db"


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DATABASE_NAME)


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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT,
            entry REAL NOT NULL,
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
            stop_hit_at TEXT
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_setup ON signals(setup_key)")

    conn.commit()
    conn.close()


def add_user(telegram_id, username, first_name):

    conn = connect()

    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR IGNORE INTO users(
            telegram_id,
            username,
            first_name
        )
        VALUES(?,?,?)
    """, (
        telegram_id,
        username,
        first_name
    ))

    conn.commit()
    conn.close()
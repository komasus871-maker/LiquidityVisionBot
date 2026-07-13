from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATABASE_NAME = DATA_DIR / "database.db"

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor


class DBRow(dict):
    """Mapping row that also supports sqlite-style numeric indexing."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class DBCursor:
    def __init__(self, cursor, *, postgres: bool):
        self._cursor = cursor
        self.postgres = postgres
        self.rowcount = getattr(cursor, "rowcount", -1)
        self.lastrowid = getattr(cursor, "lastrowid", None)

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            return DBRow(row)
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [DBRow(row) if isinstance(row, dict) else row for row in rows]


class DBConnection:
    def __init__(self, raw, *, postgres: bool):
        self.raw = raw
        self.postgres = postgres
        self.total_changes = 0

    @staticmethod
    def _translate(sql: str) -> str:
        # Project SQL uses DB-API qmark style. PostgreSQL uses %s.
        return re.sub(r"\?", "%s", sql)

    def execute(self, sql: str, params: Iterable[Any] = ()) -> DBCursor:
        if self.postgres:
            cur = self.raw.cursor(cursor_factory=RealDictCursor)
            cur.execute(self._translate(sql), tuple(params))
        else:
            cur = self.raw.execute(sql, tuple(params))
            self.total_changes = self.raw.total_changes
        return DBCursor(cur, postgres=self.postgres)

    def cursor(self):
        return self

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()
        return False


def connect() -> DBConnection:
    if USE_POSTGRES:
        raw = psycopg2.connect(DATABASE_URL, connect_timeout=15, sslmode=os.getenv("PGSSLMODE", "require"))
        raw.autocommit = False
        return DBConnection(raw, postgres=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(DATABASE_NAME, timeout=30)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA synchronous=NORMAL")
    raw.execute("PRAGMA busy_timeout=30000")
    raw.execute("PRAGMA foreign_keys=ON")
    return DBConnection(raw, postgres=False)


def database_backend() -> str:
    return "postgresql" if USE_POSTGRES else "sqlite"


def _columns(conn: DBConnection, table: str) -> set[str]:
    if conn.postgres:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchall()
        return {row[0] for row in rows}
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: DBConnection, table: str, name: str, definition: str) -> None:
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _id_column() -> str:
    return "BIGSERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"


def create_tables() -> None:
    with connect() as conn:
        id_col = _id_column()
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS users(
                id {id_col},
                telegram_id BIGINT UNIQUE,
                username TEXT,
                first_name TEXT,
                premium INTEGER DEFAULT 0,
                premium_tier TEXT DEFAULT 'FREE',
                premium_until TEXT,
                notifications_enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS signals(
                id {id_col}, owner_telegram_id BIGINT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                side TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'WATCHING', created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, triggered_at TEXT, activated_at TEXT, expires_at TEXT,
                invalidated_at TEXT, closed_at TEXT, entry DOUBLE PRECISION NOT NULL,
                preferred_entry_low DOUBLE PRECISION, preferred_entry_high DOUBLE PRECISION,
                stop DOUBLE PRECISION NOT NULL, tp1 DOUBLE PRECISION NOT NULL, tp2 DOUBLE PRECISION NOT NULL,
                tp3 DOUBLE PRECISION NOT NULL, rr DOUBLE PRECISION NOT NULL, confidence DOUBLE PRECISION NOT NULL,
                bull_score DOUBLE PRECISION NOT NULL, bear_score DOUBLE PRECISION NOT NULL,
                recommendation TEXT NOT NULL, setup_key TEXT NOT NULL, features_json TEXT NOT NULL,
                reasons_json TEXT NOT NULL, current_price DOUBLE PRECISION, max_profit_pct DOUBLE PRECISION DEFAULT 0,
                max_drawdown_pct DOUBLE PRECISION DEFAULT 0, tp1_hit_at TEXT, tp2_hit_at TEXT, tp3_hit_at TEXT,
                stop_hit_at TEXT, last_notified_status TEXT, notification_chat_id BIGINT
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS signal_events(
                id {id_col}, signal_id BIGINT NOT NULL, event_type TEXT NOT NULL, price DOUBLE PRECISION,
                details_json TEXT, created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS payments(
                id {id_col}, telegram_id BIGINT NOT NULL, provider TEXT NOT NULL, payload TEXT NOT NULL,
                amount INTEGER NOT NULL, currency TEXT NOT NULL, telegram_payment_charge_id TEXT,
                provider_payment_charge_id TEXT, created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS analysis_observations(
                id {id_col}, owner_telegram_id BIGINT, notification_chat_id BIGINT, symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL, direction TEXT NOT NULL, market_bias TEXT NOT NULL,
                execution_status TEXT NOT NULL, recommendation TEXT NOT NULL, direction_score DOUBLE PRECISION NOT NULL,
                entry_quality DOUBLE PRECISION NOT NULL, risk_quality DOUBLE PRECISION NOT NULL,
                readiness DOUBLE PRECISION NOT NULL, directional_edge DOUBLE PRECISION NOT NULL,
                price DOUBLE PRECISION NOT NULL, preferred_entry_low DOUBLE PRECISION,
                preferred_entry_high DOUBLE PRECISION, setup_key TEXT, features_json TEXT NOT NULL,
                promoted_signal_id BIGINT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS user_watchlist(
                id {id_col}, telegram_id BIGINT NOT NULL, symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL DEFAULT '1h', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(telegram_id, symbol, timeframe)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS watch_states(
                id {id_col}, telegram_id BIGINT NOT NULL, symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL DEFAULT '1h', snapshot_json TEXT NOT NULL,
                updated_at TEXT NOT NULL, last_notified_at TEXT,
                UNIQUE(telegram_id, symbol, timeframe)
            )
        """)

        for name, definition in {
            "premium_tier": "TEXT DEFAULT 'FREE'", "premium_until": "TEXT",
            "notifications_enabled": "INTEGER DEFAULT 1",
        }.items():
            _add_column(conn, "users", name, definition)
        for name, definition in {
            "owner_telegram_id": "BIGINT", "triggered_at": "TEXT", "activated_at": "TEXT",
            "expires_at": "TEXT", "invalidated_at": "TEXT", "preferred_entry_low": "DOUBLE PRECISION",
            "preferred_entry_high": "DOUBLE PRECISION", "last_notified_status": "TEXT",
            "notification_chat_id": "BIGINT",
        }.items():
            _add_column(conn, "signals", name, definition)

        for sql in (
            "CREATE INDEX IF NOT EXISTS idx_user_watchlist_owner ON user_watchlist(telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_watch_states_owner ON watch_states(telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_observations_owner ON analysis_observations(owner_telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_observations_symbol ON analysis_observations(symbol, timeframe)",
            "CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)",
            "CREATE INDEX IF NOT EXISTS idx_signals_setup ON signals(setup_key)",
            "CREATE INDEX IF NOT EXISTS idx_signals_owner ON signals(owner_telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_signal_events_signal ON signal_events(signal_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_telegram_charge ON payments(telegram_payment_charge_id) WHERE telegram_payment_charge_id IS NOT NULL",
        ):
            conn.execute(sql)


def add_user(telegram_id: int, username: str | None, first_name: str | None) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users(telegram_id, username, first_name)
            VALUES(?,?,?)
            ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name
            """,
            (telegram_id, username, first_name),
        )

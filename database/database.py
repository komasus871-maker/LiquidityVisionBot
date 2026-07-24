from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
USE_POSTGRES = DATABASE_URL.startswith("postgresql://")
REQUIRE_PERSISTENT_DB = os.getenv("REQUIRE_PERSISTENT_DB", "false").strip().lower() in {"1", "true", "yes", "on"}
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATABASE_NAME = DATA_DIR / "database.db"

if REQUIRE_PERSISTENT_DB and not USE_POSTGRES:
    raise RuntimeError(
        "Persistent database is required but DATABASE_URL is missing or invalid. "
        "Configure a PostgreSQL URL in Render, for example from Neon/Supabase/Render Postgres."
    )

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
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def connect() -> DBConnection:
    if USE_POSTGRES:
        kwargs: dict[str, Any] = {
            "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "15")),
            "application_name": "liquidity-vision-bot",
        }
        # Most hosted PostgreSQL providers require TLS. If sslmode is already
        # embedded in the URL, psycopg2 safely accepts this explicit value too.
        kwargs["sslmode"] = os.getenv("PGSSLMODE", "require")
        raw = psycopg2.connect(DATABASE_URL, **kwargs)
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


def persistent_database() -> bool:
    return USE_POSTGRES


def ping_database() -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute("SELECT 1 AS ok").fetchone()
    elapsed_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2)
    return {"ok": bool(row and row[0] == 1), "backend": database_backend(), "latency_ms": elapsed_ms}


def _columns(conn: DBConnection, table: str) -> set[str]:
    if conn.postgres:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchall()
        return {str(row[0]) for row in rows}
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: DBConnection, table: str, name: str, definition: str) -> None:
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _id_column() -> str:
    return "BIGSERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"


def create_tables() -> None:
    with connect() as conn:
        id_col = _id_column()
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS user_exchange_credentials(
                id {id_col}, telegram_id BIGINT NOT NULL, exchange TEXT NOT NULL,
                api_key_encrypted TEXT NOT NULL, api_secret_encrypted TEXT NOT NULL,
                passphrase_encrypted TEXT DEFAULT '', testnet INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'connected', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(telegram_id, exchange)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS users(
                id {id_col}, telegram_id BIGINT UNIQUE,
                username TEXT, first_name TEXT,
                premium INTEGER DEFAULT 0, premium_tier TEXT DEFAULT 'FREE',
                premium_until TEXT, notifications_enabled INTEGER DEFAULT 1,
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
                reasons_json TEXT NOT NULL, current_price DOUBLE PRECISION,
                max_profit_pct DOUBLE PRECISION DEFAULT 0, max_drawdown_pct DOUBLE PRECISION DEFAULT 0,
                tp1_hit_at TEXT, tp2_hit_at TEXT, tp3_hit_at TEXT, stop_hit_at TEXT,
                last_notified_status TEXT, notification_chat_id BIGINT,
                effective_stop DOUBLE PRECISION, break_even_at TEXT, exit_price DOUBLE PRECISION,
                realized_r DOUBLE PRECISION, result TEXT, highest_price DOUBLE PRECISION,
                lowest_price DOUBLE PRECISION, last_progress_notified_at TEXT,
                last_progress_bucket INTEGER DEFAULT -1,
                pre_activation_max_profit_pct DOUBLE PRECISION DEFAULT 0,
                pre_activation_max_drawdown_pct DOUBLE PRECISION DEFAULT 0,
                plan_locked_at TEXT,
                dynamic_confidence DOUBLE PRECISION, previous_confidence DOUBLE PRECISION,
                trade_health TEXT, health_score DOUBLE PRECISION, intelligence_json TEXT,
                last_intelligence_notified_at TEXT, last_alert_signature TEXT,
                last_risk_used DOUBLE PRECISION DEFAULT 0, last_mfe_giveback DOUBLE PRECISION DEFAULT 0
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS signal_events(
                id {id_col}, signal_id BIGINT NOT NULL, event_type TEXT NOT NULL,
                price DOUBLE PRECISION, details_json TEXT, created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS trade_memories(
                id {id_col}, signal_id BIGINT NOT NULL UNIQUE, dna_fingerprint TEXT,
                memory_json TEXT NOT NULL, lesson TEXT NOT NULL, result TEXT,
                realized_r DOUBLE PRECISION DEFAULT 0, created_at TEXT NOT NULL
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
                id {id_col}, owner_telegram_id BIGINT, notification_chat_id BIGINT,
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, direction TEXT NOT NULL,
                market_bias TEXT NOT NULL, execution_status TEXT NOT NULL, recommendation TEXT NOT NULL,
                direction_score DOUBLE PRECISION NOT NULL, entry_quality DOUBLE PRECISION NOT NULL,
                risk_quality DOUBLE PRECISION NOT NULL, readiness DOUBLE PRECISION NOT NULL,
                directional_edge DOUBLE PRECISION NOT NULL, price DOUBLE PRECISION NOT NULL,
                preferred_entry_low DOUBLE PRECISION, preferred_entry_high DOUBLE PRECISION,
                setup_key TEXT, features_json TEXT NOT NULL, promoted_signal_id BIGINT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS signal_candidates(
                id {id_col}, owner_telegram_id BIGINT NOT NULL, notification_chat_id BIGINT,
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, side TEXT NOT NULL,
                observation_id BIGINT, blocked_by_signal_id BIGINT,
                status TEXT NOT NULL DEFAULT 'PENDING', snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                resolved_at TEXT, promoted_signal_id BIGINT,
                UNIQUE(owner_telegram_id, symbol, timeframe, side)
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
                timeframe TEXT NOT NULL DEFAULT '1h', snapshot_json TEXT NOT NULL DEFAULT '{{}}',
                updated_at TEXT NOT NULL, last_checked_at TEXT, last_notified_at TEXT,
                last_error TEXT, consecutive_errors INTEGER DEFAULT 0, promoted_signal_id BIGINT,
                UNIQUE(telegram_id, symbol, timeframe)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS watch_events(
                id {id_col}, telegram_id BIGINT NOT NULL, symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL, event_type TEXT NOT NULL, details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runtime_state(
                worker_name TEXT PRIMARY KEY, last_started_at TEXT, last_finished_at TEXT,
                last_success_at TEXT, last_error TEXT, processed_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0, details_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS distributed_leases(
                lease_name TEXT PRIMARY KEY, owner_id TEXT NOT NULL, expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS copy_profiles(
                id {id_col}, telegram_id BIGINT NOT NULL UNIQUE, enabled INTEGER DEFAULT 0,
                mode TEXT NOT NULL DEFAULT 'PAPER', exchange TEXT, risk_pct DOUBLE PRECISION DEFAULT 0.5,
                sizing_mode TEXT NOT NULL DEFAULT 'RISK_PERCENT', fixed_usdt DOUBLE PRECISION DEFAULT 0,
                leverage INTEGER DEFAULT 1, auto_copy INTEGER DEFAULT 0, max_positions INTEGER DEFAULT 3, max_heat_r DOUBLE PRECISION DEFAULT 2.5,
                daily_loss_pct DOUBLE PRECISION DEFAULT 2.0, max_slippage_pct DOUBLE PRECISION DEFAULT 0.25,
                paper_balance DOUBLE PRECISION DEFAULT 10000, min_confidence DOUBLE PRECISION DEFAULT 55,
                max_notional_pct DOUBLE PRECISION DEFAULT 35, symbol_cooldown_min INTEGER DEFAULT 30,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS paper_positions(
                id {id_col}, telegram_id BIGINT NOT NULL, signal_id BIGINT NOT NULL, symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL, side TEXT NOT NULL, status TEXT NOT NULL, entry_price DOUBLE PRECISION,
                last_price DOUBLE PRECISION, exit_price DOUBLE PRECISION, stop_price DOUBLE PRECISION,
                tp1 DOUBLE PRECISION, tp2 DOUBLE PRECISION, tp3 DOUBLE PRECISION, quantity DOUBLE PRECISION,
                notional DOUBLE PRECISION, risk_amount DOUBLE PRECISION, initial_risk_r DOUBLE PRECISION DEFAULT 1.0,
                remaining_fraction DOUBLE PRECISION DEFAULT 1.0, realized_r DOUBLE PRECISION DEFAULT 0,
                rejection_code TEXT, rejection_reason TEXT, close_reason TEXT, realized_pnl DOUBLE PRECISION DEFAULT 0,
                last_signal_status TEXT, opened_at TEXT, closed_at TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, UNIQUE(telegram_id,signal_id)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS execution_events(
                id {id_col}, telegram_id BIGINT NOT NULL, signal_id BIGINT, event_type TEXT NOT NULL,
                price DOUBLE PRECISION, realized_pnl_delta DOUBLE PRECISION DEFAULT 0,
                details_json TEXT NOT NULL, created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS copy_execution_journal(
                id {id_col}, idempotency_key TEXT NOT NULL UNIQUE, plan_id TEXT NOT NULL,
                telegram_id BIGINT NOT NULL, signal_id BIGINT NOT NULL, exchange_account_id BIGINT,
                status TEXT NOT NULL, code TEXT NOT NULL, reason TEXT NOT NULL, plan_json TEXT NOT NULL,
                attempt_count INTEGER DEFAULT 0, execution_ref TEXT, last_error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations(
                version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL
            )
        """)
        schema_version = int(os.getenv("SCHEMA_VERSION", "1"))
        conn.execute(
            "INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?) ON CONFLICT(version) DO NOTHING",
            (schema_version, f"baseline_v{schema_version}", datetime.now(timezone.utc).isoformat()),
        )

        for name, definition in {
            "premium_tier": "TEXT DEFAULT 'FREE'", "premium_until": "TEXT",
            "notifications_enabled": "INTEGER DEFAULT 1",
        }.items():
            _add_column(conn, "users", name, definition)
        for name, definition in {
            "owner_telegram_id": "BIGINT", "triggered_at": "TEXT", "activated_at": "TEXT",
            "expires_at": "TEXT", "invalidated_at": "TEXT", "preferred_entry_low": "DOUBLE PRECISION",
            "preferred_entry_high": "DOUBLE PRECISION", "last_notified_status": "TEXT",
            "notification_chat_id": "BIGINT", "effective_stop": "DOUBLE PRECISION",
            "break_even_at": "TEXT", "exit_price": "DOUBLE PRECISION",
            "realized_r": "DOUBLE PRECISION", "result": "TEXT",
            "highest_price": "DOUBLE PRECISION", "lowest_price": "DOUBLE PRECISION",
            "last_progress_notified_at": "TEXT", "last_progress_bucket": "INTEGER DEFAULT -1",
            "pre_activation_max_profit_pct": "DOUBLE PRECISION DEFAULT 0",
            "pre_activation_max_drawdown_pct": "DOUBLE PRECISION DEFAULT 0",
            "plan_locked_at": "TEXT",
            "dynamic_confidence": "DOUBLE PRECISION", "previous_confidence": "DOUBLE PRECISION",
            "trade_health": "TEXT", "health_score": "DOUBLE PRECISION", "intelligence_json": "TEXT",
            "last_intelligence_notified_at": "TEXT", "last_alert_signature": "TEXT",
            "last_risk_used": "DOUBLE PRECISION DEFAULT 0", "last_mfe_giveback": "DOUBLE PRECISION DEFAULT 0",
            "trade_dna_json": "TEXT", "dna_fingerprint": "TEXT", "memory_created_at": "TEXT",
        }.items():
            _add_column(conn, "signals", name, definition)
        for name, definition in {
            "last_checked_at": "TEXT", "last_error": "TEXT",
            "consecutive_errors": "INTEGER DEFAULT 0", "promoted_signal_id": "BIGINT",
        }.items():
            _add_column(conn, "watch_states", name, definition)
        for name, definition in {
            "min_confidence": "DOUBLE PRECISION DEFAULT 55",
            "max_notional_pct": "DOUBLE PRECISION DEFAULT 35",
            "symbol_cooldown_min": "INTEGER DEFAULT 30",
            "sizing_mode": "TEXT NOT NULL DEFAULT 'RISK_PERCENT'",
            "fixed_usdt": "DOUBLE PRECISION DEFAULT 0",
            "leverage": "INTEGER DEFAULT 1",
            "auto_copy": "INTEGER DEFAULT 0",
        }.items():
            _add_column(conn, "copy_profiles", name, definition)
        for name, definition in {
            "realized_pnl": "DOUBLE PRECISION DEFAULT 0",
            "last_signal_status": "TEXT",
            "shadow_exit_price": "DOUBLE PRECISION",
            "shadow_realized_r": "DOUBLE PRECISION",
            "shadow_result": "TEXT",
            "shadow_closed_at": "TEXT",
            "genome_json": "TEXT",
            "genome_fingerprint": "TEXT",
        }.items():
            _add_column(conn, "paper_positions", name, definition)
        _add_column(conn, "execution_events", "realized_pnl_delta", "DOUBLE PRECISION DEFAULT 0")

        # Reconcile legacy duplicate open plans before enforcing uniqueness.
        duplicate_groups = conn.execute("""
            SELECT COALESCE(owner_telegram_id,0) owner_key, symbol, timeframe, COUNT(*) cnt
            FROM signals
            WHERE status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')
            GROUP BY COALESCE(owner_telegram_id,0), symbol, timeframe
            HAVING COUNT(*) > 1
        """).fetchall()
        now_reconcile = datetime.now(timezone.utc).isoformat()
        priority = {"TP2": 5, "TP1": 4, "ACTIVE": 3, "TRIGGERED": 2, "WATCHING": 1}
        for group in duplicate_groups:
            owner_key = group["owner_key"] if isinstance(group, dict) else group[0]
            symbol = group["symbol"] if isinstance(group, dict) else group[1]
            timeframe = group["timeframe"] if isinstance(group, dict) else group[2]
            rows = conn.execute("""
                SELECT * FROM signals
                WHERE COALESCE(owner_telegram_id,0)=? AND symbol=? AND timeframe=?
                  AND status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')
                ORDER BY id DESC
            """, (owner_key, symbol, timeframe)).fetchall()
            rows = [dict(r) for r in rows]
            keep = max(rows, key=lambda r: (priority.get(str(r.get("status")), 0), int(r.get("id") or 0)))
            for row in rows:
                if int(row["id"]) == int(keep["id"]):
                    continue
                price = row.get("current_price") if row.get("current_price") is not None else row.get("entry")
                conn.execute(
                    "UPDATE signals SET status='INVALIDATED', invalidated_at=?, closed_at=?, updated_at=?, result='LEGACY_DUPLICATE_RECONCILED' WHERE id=?",
                    (now_reconcile, now_reconcile, now_reconcile, row["id"]),
                )
                conn.execute(
                    "INSERT INTO signal_events(signal_id,event_type,price,details_json,created_at) VALUES(?,?,?,?,?)",
                    (row["id"], "DUPLICATE_RECONCILED", price,
                     json.dumps({"kept_signal_id": keep["id"]}, ensure_ascii=False), now_reconcile),
                )

        for sql in (
            "CREATE INDEX IF NOT EXISTS idx_copy_profiles_enabled ON copy_profiles(enabled,mode)",
            "CREATE INDEX IF NOT EXISTS idx_paper_positions_owner_status ON paper_positions(telegram_id,status)",
            "CREATE INDEX IF NOT EXISTS idx_paper_positions_signal ON paper_positions(signal_id)",
            "CREATE INDEX IF NOT EXISTS idx_paper_positions_genome ON paper_positions(genome_fingerprint)",
            "CREATE INDEX IF NOT EXISTS idx_execution_events_owner ON execution_events(telegram_id,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_user_watchlist_owner ON user_watchlist(telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_watch_states_owner ON watch_states(telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_watch_events_owner ON watch_events(telegram_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_observations_owner ON analysis_observations(owner_telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_observations_symbol ON analysis_observations(symbol, timeframe)",
            "CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)",
            "CREATE INDEX IF NOT EXISTS idx_signals_setup ON signals(setup_key)",
            "CREATE INDEX IF NOT EXISTS idx_signals_owner ON signals(owner_telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_signal_events_signal ON signal_events(signal_id)",
            "CREATE INDEX IF NOT EXISTS idx_signals_dna_fingerprint ON signals(dna_fingerprint)",
            "CREATE INDEX IF NOT EXISTS idx_trade_memories_fingerprint ON trade_memories(dna_fingerprint)",
            "CREATE INDEX IF NOT EXISTS idx_candidates_owner ON signal_candidates(owner_telegram_id, updated_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_market_plan ON signals(COALESCE(owner_telegram_id,0),symbol,timeframe) WHERE status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_telegram_charge ON payments(telegram_payment_charge_id) WHERE telegram_payment_charge_id IS NOT NULL",
        ):
            conn.execute(sql)


def add_user(telegram_id: int, username: str | None, first_name: str | None) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users(telegram_id, username, first_name)
            VALUES(?,?,?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
            """,
            (telegram_id, username, first_name),
        )


def acquire_lease(lease_name: str, owner_id: str, ttl_seconds: int) -> bool:
    """Atomically acquire a cross-process lease on SQLite or PostgreSQL."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=max(30, ttl_seconds))
    now_s, expires_s = now.isoformat(), expires.isoformat()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO distributed_leases(lease_name,owner_id,expires_at,updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(lease_name) DO UPDATE SET
                owner_id=excluded.owner_id,expires_at=excluded.expires_at,updated_at=excluded.updated_at
            WHERE distributed_leases.expires_at<=? OR distributed_leases.owner_id=?
            """,
            (lease_name, owner_id, expires_s, now_s, now_s, owner_id),
        )
        return cur.rowcount > 0


def release_lease(lease_name: str, owner_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM distributed_leases WHERE lease_name=? AND owner_id=?", (lease_name, owner_id))


def runtime_started(worker_name: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO runtime_state(worker_name,last_started_at,processed_count,error_count)
            VALUES(?,?,0,0)
            ON CONFLICT(worker_name) DO UPDATE SET last_started_at=excluded.last_started_at,last_error=NULL
            """,
            (worker_name, now),
        )


def runtime_finished(worker_name: str, *, processed: int, errors: int, details: dict[str, Any] | None = None, error: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    details_json = json.dumps(details or {}, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO runtime_state(worker_name,last_finished_at,last_success_at,last_error,processed_count,error_count,details_json)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(worker_name) DO UPDATE SET
                last_finished_at=excluded.last_finished_at,
                last_success_at=CASE WHEN excluded.last_error IS NULL THEN excluded.last_finished_at ELSE runtime_state.last_success_at END,
                last_error=excluded.last_error,
                processed_count=excluded.processed_count,
                error_count=excluded.error_count,
                details_json=excluded.details_json
            """,
            (worker_name, now, None if error else now, error, processed, errors, details_json),
        )


def get_runtime_states() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM runtime_state ORDER BY worker_name").fetchall()
    return [dict(row) for row in rows]

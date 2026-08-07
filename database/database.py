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
    if name in _columns(conn, table):
        return
    if conn.postgres:
        conn.execute("SAVEPOINT add_column_guard")
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    except Exception as exc:
        # Rolling deployments can run two schema initializers concurrently.
        # PostgreSQL aborts the current transaction after duplicate_column, so
        # use a savepoint to make that benign race recoverable without hiding
        # any other migration failure. SQLite serializes ALTER TABLE writes.
        duplicate_column = (getattr(exc, "pgcode", None) == "42701" or
                            "duplicate column name" in str(exc).lower())
        if conn.postgres:
            conn.execute("ROLLBACK TO SAVEPOINT add_column_guard")
            conn.execute("RELEASE SAVEPOINT add_column_guard")
        if not duplicate_column:
            raise
    else:
        if conn.postgres:
            conn.execute("RELEASE SAVEPOINT add_column_guard")


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
                profile_name TEXT NOT NULL DEFAULT 'STANDARD',
                sizing_mode TEXT NOT NULL DEFAULT 'RISK_PERCENT', fixed_usdt DOUBLE PRECISION DEFAULT 0,
                equity_pct DOUBLE PRECISION DEFAULT 10, copy_multiplier DOUBLE PRECISION DEFAULT 1,
                leverage INTEGER DEFAULT 1, auto_copy INTEGER DEFAULT 0, max_positions INTEGER DEFAULT 3, max_heat_r DOUBLE PRECISION DEFAULT 2.5,
                daily_loss_pct DOUBLE PRECISION DEFAULT 2.0, max_slippage_pct DOUBLE PRECISION DEFAULT 0.25,
                paper_balance DOUBLE PRECISION DEFAULT 10000, min_confidence DOUBLE PRECISION DEFAULT 55,
                max_notional_pct DOUBLE PRECISION DEFAULT 35, symbol_cooldown_min INTEGER DEFAULT 30,
                max_portfolio_exposure_pct DOUBLE PRECISION DEFAULT 70,
                symbol_policy TEXT NOT NULL DEFAULT 'ALL', symbol_whitelist_json TEXT NOT NULL DEFAULT '[]',
                symbol_blacklist_json TEXT NOT NULL DEFAULT '[]', timeframe_filters_json TEXT NOT NULL DEFAULT '[]',
                setup_filters_json TEXT NOT NULL DEFAULT '[]', direction_filters_json TEXT NOT NULL DEFAULT '[]',
                allow_experimental INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS copy_profile_events(
                id {id_col}, telegram_id BIGINT NOT NULL, event_type TEXT NOT NULL,
                actor TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
                changed_fields_json TEXT NOT NULL, created_at TEXT NOT NULL
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
                details_json TEXT NOT NULL, source_event_key TEXT, created_at TEXT NOT NULL
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
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS execution_transition_events(
                id {id_col}, idempotency_key TEXT NOT NULL, telegram_id BIGINT NOT NULL,
                signal_id BIGINT NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
                actor TEXT NOT NULL, reason_code TEXT, reason TEXT, execution_ref TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{{}}', created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS paper_execution_orders(
                id {id_col}, order_key TEXT NOT NULL UNIQUE, idempotency_key TEXT NOT NULL UNIQUE,
                plan_id TEXT NOT NULL, telegram_id BIGINT NOT NULL, signal_id BIGINT NOT NULL,
                execution_ref TEXT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL, side TEXT NOT NULL,
                order_type TEXT NOT NULL, status TEXT NOT NULL, requested_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
                filled_quantity DOUBLE PRECISION NOT NULL DEFAULT 0, average_fill_price DOUBLE PRECISION,
                limit_price DOUBLE PRECISION, notional DOUBLE PRECISION, leverage INTEGER NOT NULL DEFAULT 1,
                stop_loss DOUBLE PRECISION, risk_amount DOUBLE PRECISION,
                last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS paper_execution_fills(
                id {id_col}, fill_key TEXT NOT NULL UNIQUE, order_id BIGINT NOT NULL,
                idempotency_key TEXT NOT NULL, telegram_id BIGINT NOT NULL, signal_id BIGINT NOT NULL,
                execution_ref TEXT, quantity DOUBLE PRECISION NOT NULL, price DOUBLE PRECISION NOT NULL,
                notional DOUBLE PRECISION NOT NULL, commission DOUBLE PRECISION NOT NULL DEFAULT 0,
                commission_rate DOUBLE PRECISION NOT NULL DEFAULT 0, slippage_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
                liquidity_type TEXT NOT NULL DEFAULT 'TAKER', created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS paper_execution_positions(
                id {id_col}, position_key TEXT NOT NULL UNIQUE, order_id BIGINT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL, telegram_id BIGINT NOT NULL, signal_id BIGINT NOT NULL,
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, side TEXT NOT NULL, status TEXT NOT NULL,
                quantity DOUBLE PRECISION NOT NULL DEFAULT 0, average_entry DOUBLE PRECISION NOT NULL DEFAULT 0,
                last_price DOUBLE PRECISION, realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
                unrealized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0, total_commission DOUBLE PRECISION NOT NULL DEFAULT 0,
                initial_quantity DOUBLE PRECISION, stop_loss DOUBLE PRECISION,
                initial_risk_amount DOUBLE PRECISION,
                remaining_fraction DOUBLE PRECISION NOT NULL DEFAULT 1,
                realized_r DOUBLE PRECISION NOT NULL DEFAULT 0,
                close_reason TEXT, last_signal_status TEXT,
                opened_at TEXT, closed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS paper_position_lifecycle_events(
                id {id_col}, event_key TEXT NOT NULL UNIQUE, position_id BIGINT NOT NULL,
                idempotency_key TEXT NOT NULL, telegram_id BIGINT NOT NULL, signal_id BIGINT NOT NULL,
                event_type TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
                signal_status TEXT, price DOUBLE PRECISION,
                quantity_before DOUBLE PRECISION NOT NULL DEFAULT 0,
                quantity_after DOUBLE PRECISION NOT NULL DEFAULT 0,
                realized_pnl_delta DOUBLE PRECISION NOT NULL DEFAULT 0,
                realized_r_delta DOUBLE PRECISION NOT NULL DEFAULT 0,
                reason TEXT, created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS paper_portfolio_ledger(
                id {id_col}, source_key TEXT NOT NULL UNIQUE, telegram_id BIGINT NOT NULL,
                position_id BIGINT, order_id BIGINT, entry_type TEXT NOT NULL,
                amount DOUBLE PRECISION NOT NULL, realized_r_delta DOUBLE PRECISION NOT NULL DEFAULT 0,
                symbol TEXT, occurred_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{{}}',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS historical_execution_records(
                id {id_col}, source_key TEXT NOT NULL UNIQUE,
                legacy_position_id BIGINT NOT NULL UNIQUE, telegram_id BIGINT NOT NULL,
                signal_id BIGINT, linked_unified_position_id BIGINT,
                classification TEXT NOT NULL, migration_status TEXT NOT NULL,
                symbol TEXT, timeframe TEXT, side TEXT, legacy_status TEXT,
                entry_price DOUBLE PRECISION, exit_price DOUBLE PRECISION,
                quantity DOUBLE PRECISION, notional DOUBLE PRECISION,
                risk_amount DOUBLE PRECISION, realized_pnl DOUBLE PRECISION,
                realized_r DOUBLE PRECISION, commission DOUBLE PRECISION,
                opened_at TEXT, closed_at TEXT, source_created_at TEXT,
                price_provenance TEXT NOT NULL, risk_provenance TEXT NOT NULL,
                commission_provenance TEXT NOT NULL, provenance_json TEXT NOT NULL,
                source_checksum TEXT NOT NULL, migrated_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS historical_migration_runs(
                id {id_col}, run_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
                scanned_count INTEGER NOT NULL DEFAULT 0, migrated_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0, unresolved_count INTEGER NOT NULL DEFAULT 0,
                classification_json TEXT NOT NULL DEFAULT '{{}}', last_legacy_position_id BIGINT,
                started_at TEXT NOT NULL, completed_at TEXT, error TEXT
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS live_exchange_accounts(
                id {id_col}, telegram_id BIGINT NOT NULL, exchange TEXT NOT NULL,
                credential_ref TEXT NOT NULL, execution_mode TEXT NOT NULL DEFAULT 'PAPER',
                live_enabled INTEGER NOT NULL DEFAULT 0, dry_run_enabled INTEGER NOT NULL DEFAULT 0,
                confirmation_hash TEXT, confirmation_expires_at TEXT, confirmed_at TEXT,
                kill_switch INTEGER NOT NULL DEFAULT 1, max_order_notional DOUBLE PRECISION,
                max_account_exposure DOUBLE PRECISION, max_leverage INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(telegram_id, exchange)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS live_executions(
                id {id_col}, execution_key TEXT NOT NULL UNIQUE, plan_id TEXT,
                telegram_id BIGINT NOT NULL, account_id BIGINT NOT NULL, exchange TEXT NOT NULL,
                mode TEXT NOT NULL, client_order_id TEXT NOT NULL, exchange_order_id TEXT,
                symbol TEXT NOT NULL, side TEXT NOT NULL, order_type TEXT NOT NULL,
                quantity DOUBLE PRECISION NOT NULL, price DOUBLE PRECISION, reduce_only INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL, executed_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
                average_fill_price DOUBLE PRECISION, commission DOUBLE PRECISION NOT NULL DEFAULT 0,
                next_retry_at TEXT,
                recovery_reason TEXT, version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(account_id, client_order_id)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS live_execution_attempts(
                id {id_col}, execution_id BIGINT NOT NULL, attempt_number INTEGER NOT NULL,
                client_order_id TEXT NOT NULL, adapter TEXT NOT NULL, account_id BIGINT NOT NULL,
                request_checksum TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
                exchange_order_id TEXT, normalized_error_code TEXT, normalized_error TEXT,
                raw_response_checksum TEXT, retry_at TEXT, started_at TEXT NOT NULL, completed_at TEXT,
                UNIQUE(execution_id, attempt_number)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS live_execution_fills(
                id {id_col}, execution_id BIGINT NOT NULL, account_id BIGINT NOT NULL,
                exchange_fill_id TEXT NOT NULL, exchange_order_id TEXT NOT NULL,
                quantity DOUBLE PRECISION NOT NULL, price DOUBLE PRECISION NOT NULL,
                commission DOUBLE PRECISION NOT NULL DEFAULT 0, commission_asset TEXT,
                filled_at TEXT, created_at TEXT NOT NULL,
                UNIQUE(account_id, exchange_fill_id)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS live_readiness_audits(
                id {id_col}, telegram_id BIGINT NOT NULL, account_id BIGINT,
                exchange TEXT NOT NULL, requested_mode TEXT NOT NULL, ready INTEGER NOT NULL,
                reason_codes_json TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS bingx_certification_audits(
                id {id_col}, run_key TEXT NOT NULL UNIQUE, telegram_id BIGINT NOT NULL,
                account_id BIGINT NOT NULL, environment TEXT NOT NULL, adapter_version TEXT NOT NULL,
                certification_type TEXT NOT NULL, status TEXT NOT NULL, symbol TEXT NOT NULL,
                capability_snapshot_json TEXT NOT NULL, permission_snapshot_json TEXT NOT NULL,
                report_json TEXT NOT NULL, server_time_drift_ms BIGINT, account_mode TEXT,
                margin_mode TEXT, started_at TEXT NOT NULL, completed_at TEXT,
                expires_at TEXT, error_code TEXT
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS exchange_symbol_rules_cache(
                id {id_col}, account_id BIGINT NOT NULL, exchange TEXT NOT NULL, environment TEXT NOT NULL,
                symbol TEXT NOT NULL, price_tick TEXT NOT NULL, quantity_step TEXT NOT NULL,
                min_quantity TEXT NOT NULL, min_notional TEXT, max_quantity TEXT, max_leverage INTEGER,
                adapter_version TEXT NOT NULL, fetched_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                UNIQUE(account_id, exchange, environment, symbol)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_prompt_versions(
                id {id_col}, prompt_version TEXT NOT NULL UNIQUE, prompt_checksum TEXT NOT NULL,
                system_prompt TEXT NOT NULL, response_schema_json TEXT NOT NULL,
                schema_version TEXT, schema_checksum TEXT, context_version TEXT,
                request_format_version TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_user_settings(
                telegram_id BIGINT PRIMARY KEY, mode TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_decisions(
                id {id_col}, decision_id TEXT NOT NULL UNIQUE, idempotency_key TEXT NOT NULL UNIQUE,
                correlation_id TEXT NOT NULL, telegram_id BIGINT, signal_id BIGINT NOT NULL,
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, market_timestamp TEXT NOT NULL,
                market_snapshot_checksum TEXT NOT NULL, feature_snapshot_checksum TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT, model_version TEXT, prompt_version TEXT NOT NULL,
                requested_mode TEXT NOT NULL, regime TEXT NOT NULL, direction TEXT NOT NULL,
                raw_confidence DOUBLE PRECISION NOT NULL, calibrated_confidence DOUBLE PRECISION,
                uncertainty DOUBLE PRECISION NOT NULL, recommended_action TEXT NOT NULL,
                recommended_risk_multiplier DOUBLE PRECISION NOT NULL, abstention INTEGER NOT NULL,
                supporting_factors_json TEXT NOT NULL, conflicting_factors_json TEXT NOT NULL,
                invalidation_conditions_json TEXT NOT NULL, explanation TEXT NOT NULL,
                schema_valid INTEGER NOT NULL, validation_code TEXT NOT NULL,
                latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0, estimated_cost_usd NUMERIC(18,8) NOT NULL DEFAULT 0,
                raw_response_checksum TEXT, deterministic_accepted INTEGER,
                deterministic_action TEXT, calibration_model_version TEXT,
                calibration_sample_size INTEGER NOT NULL DEFAULT 0,
                calibration_reliable INTEGER NOT NULL DEFAULT 0,
                provider_protocol TEXT, schema_version TEXT, schema_checksum TEXT,
                context_version TEXT, request_format_version TEXT,
                requested_output_mode TEXT, effective_output_mode TEXT, downgrade_reason TEXT,
                validation_stage TEXT, pricing_version TEXT, cost_status TEXT,
                cached_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                provider_request_id TEXT, provider_usage_json TEXT,
                provider_identity_checksum TEXT, provider_endpoint_redacted TEXT,
                capability_snapshot_json TEXT, reasoning_effort TEXT,
                extraction_stage TEXT, extraction_code TEXT, raw_envelope_checksum TEXT,
                provider_invoked INTEGER NOT NULL DEFAULT 0, legacy_classification TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_request_claims(
                idempotency_key TEXT PRIMARY KEY, claimed_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_decision_outcomes(
                id {id_col}, decision_id TEXT NOT NULL UNIQUE, signal_result TEXT,
                signal_mfe DOUBLE PRECISION, signal_mae DOUBLE PRECISION,
                direction_correct INTEGER, time_to_movement_seconds BIGINT,
                deterministic_result TEXT, execution_result TEXT,
                realized_pnl DOUBLE PRECISION, realized_r DOUBLE PRECISION,
                fees DOUBLE PRECISION, slippage_pct DOUBLE PRECISION,
                intervention_type TEXT, intervention_delta_r DOUBLE PRECISION,
                hypothetical_result TEXT, counterfactual_result TEXT,
                attached_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_calibration_snapshots(
                id {id_col}, scope_key TEXT NOT NULL, model_version TEXT NOT NULL,
                sample_size INTEGER NOT NULL, brier_score DOUBLE PRECISION,
                expected_calibration_error DOUBLE PRECISION, reliability_status TEXT NOT NULL,
                reliability_json TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(scope_key,model_version,created_at)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_provider_state(
                provider TEXT PRIMARY KEY, state TEXT NOT NULL, consecutive_failures INTEGER NOT NULL DEFAULT 0,
                opened_until TEXT, last_success_at TEXT, last_failure_at TEXT,
                last_error_code TEXT, identity_checksum TEXT, updated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_provider_certifications(
                id {id_col}, certification_id TEXT NOT NULL UNIQUE,
                identity_checksum TEXT NOT NULL, provider TEXT NOT NULL, protocol TEXT NOT NULL,
                endpoint_redacted TEXT NOT NULL, model TEXT NOT NULL, model_version TEXT NOT NULL,
                prompt_version TEXT NOT NULL, schema_version TEXT NOT NULL,
                context_version TEXT NOT NULL, request_format_version TEXT NOT NULL,
                pricing_version TEXT, capability_snapshot_json TEXT NOT NULL,
                schema_checksum TEXT, reasoning_effort TEXT, requested_output_mode TEXT,
                effective_output_mode TEXT, status TEXT NOT NULL, checks_json TEXT NOT NULL,
                failure_code TEXT, validation_stage TEXT, validation_code TEXT,
                provider_request_id TEXT, returned_model_version TEXT,
                raw_envelope_checksum TEXT, latency_ms DOUBLE PRECISION,
                input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
                cached_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost_usd NUMERIC(18,8) NOT NULL DEFAULT 0, cost_status TEXT,
                started_at TEXT, completed_at TEXT, certified_at TEXT NOT NULL, expires_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_certification_claims(
                identity_checksum TEXT PRIMARY KEY, certification_id TEXT NOT NULL,
                claimed_at TEXT NOT NULL, expires_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_governance_events(
                id {id_col}, provider TEXT NOT NULL, identity_checksum TEXT,
                from_state TEXT, to_state TEXT NOT NULL, reason_code TEXT NOT NULL,
                actor_telegram_id BIGINT, details_json TEXT NOT NULL, created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_global_control(
                control_key TEXT PRIMARY KEY, enabled INTEGER NOT NULL,
                reason_code TEXT NOT NULL, actor_telegram_id BIGINT, updated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_drift_baselines(
                id {id_col}, identity_checksum TEXT NOT NULL, scope_key TEXT NOT NULL,
                sample_size INTEGER NOT NULL, metrics_json TEXT NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(identity_checksum,scope_key)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_experiments(
                id {id_col}, experiment_key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                status TEXT NOT NULL, variants_json TEXT NOT NULL, allocation_salt TEXT NOT NULL,
                started_at TEXT, ended_at TEXT, created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_cost_reconciliations(
                id {id_col}, provider TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
                internal_cost_usd NUMERIC(18,8) NOT NULL, provider_cost_usd NUMERIC(18,8),
                variance_usd NUMERIC(18,8), status TEXT NOT NULL, details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_governance_evidence(
                id {id_col}, identity_checksum TEXT NOT NULL, target_state TEXT NOT NULL,
                decision_count INTEGER NOT NULL, schema_valid_count INTEGER NOT NULL,
                semantic_valid_count INTEGER NOT NULL, transport_failure_count INTEGER NOT NULL,
                timeout_count INTEGER NOT NULL, blockers_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL, created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_observation_events(
                id {id_col}, event_key TEXT NOT NULL UNIQUE, signal_id BIGINT,
                telegram_id BIGINT, identity_checksum TEXT, snapshot_checksum TEXT,
                status TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_decision_intelligence(
                id {id_col}, decision_id TEXT NOT NULL UNIQUE, signal_id BIGINT NOT NULL,
                identity_checksum TEXT, deterministic_decision_json TEXT NOT NULL,
                gpt_counterfactual_json TEXT NOT NULL, market_regimes_json TEXT NOT NULL,
                opportunity_quality DOUBLE PRECISION NOT NULL DEFAULT 0,
                evidence_ranking_json TEXT NOT NULL, contradictions_json TEXT NOT NULL,
                uncertainty_explanation TEXT NOT NULL, similarity_summary_json TEXT NOT NULL,
                cluster_key TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_decision_similarities(
                id {id_col}, source_decision_id TEXT NOT NULL, similar_decision_id TEXT NOT NULL,
                similar_signal_id BIGINT NOT NULL, similarity_score DOUBLE PRECISION NOT NULL,
                matching_json TEXT NOT NULL, differences_json TEXT NOT NULL,
                outcome_r DOUBLE PRECISION, created_at TEXT NOT NULL,
                UNIQUE(source_decision_id,similar_decision_id)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_counterfactual_evaluations(
                id {id_col}, decision_id TEXT NOT NULL UNIQUE, signal_id BIGINT NOT NULL,
                identity_checksum TEXT, primary_regime TEXT NOT NULL,
                deterministic_positive INTEGER NOT NULL, gpt_positive INTEGER NOT NULL,
                actual_positive INTEGER NOT NULL, classification TEXT NOT NULL,
                deterministic_correct INTEGER NOT NULL, gpt_correct INTEGER NOT NULL,
                disagreement INTEGER NOT NULL, profitable_disagreement INTEGER NOT NULL,
                opportunity_quality DOUBLE PRECISION NOT NULL DEFAULT 0,
                realized_r DOUBLE PRECISION NOT NULL DEFAULT 0, intervention_type TEXT,
                evaluation_eligible INTEGER NOT NULL DEFAULT 1, evaluated_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_learning_snapshots(
                id {id_col}, snapshot_key TEXT NOT NULL UNIQUE, identity_checksum TEXT,
                sample_size INTEGER NOT NULL, expectancy_r DOUBLE PRECISION,
                precision_score DOUBLE PRECISION, recall_score DOUBLE PRECISION,
                evidence_json TEXT NOT NULL, indicators_json TEXT NOT NULL,
                recurring_failures_json TEXT NOT NULL, regimes_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_observation_queue_snapshots(
                id {id_col}, identity_checksum TEXT, queued INTEGER NOT NULL,
                processed INTEGER NOT NULL, dropped INTEGER NOT NULL, failed INTEGER NOT NULL,
                cancelled INTEGER NOT NULL DEFAULT 0, duration_ms DOUBLE PRECISION NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ai_provider_request_events(
                id {id_col}, event_key TEXT NOT NULL UNIQUE, identity_checksum TEXT,
                signal_id BIGINT, attempt_number INTEGER NOT NULL, status TEXT NOT NULL,
                reason_code TEXT NOT NULL, latency_ms DOUBLE PRECISION,
                provider_request_id TEXT, created_at TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS research_signal_snapshots(
                id {id_col}, snapshot_id TEXT NOT NULL UNIQUE, signal_id BIGINT NOT NULL UNIQUE,
                owner_telegram_id BIGINT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                side TEXT NOT NULL, strategy_key TEXT NOT NULL, setup_family TEXT,
                decision_at TEXT NOT NULL, captured_at TEXT NOT NULL, capture_quality TEXT NOT NULL,
                feature_version TEXT NOT NULL, source_checksum TEXT NOT NULL,
                primary_regime TEXT NOT NULL, regimes_json TEXT NOT NULL,
                confidence_bucket TEXT NOT NULL, session_key TEXT NOT NULL,
                snapshot_json TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS research_outcomes(
                id {id_col}, snapshot_id TEXT NOT NULL, signal_id BIGINT NOT NULL,
                outcome_checksum TEXT NOT NULL, outcome_version INTEGER NOT NULL,
                signal_result TEXT, signal_r DOUBLE PRECISION, mfe_pct DOUBLE PRECISION,
                mae_pct DOUBLE PRECISION, tp_progression_json TEXT NOT NULL,
                stop_reached INTEGER NOT NULL DEFAULT 0, policy_outcomes_json TEXT NOT NULL,
                execution_outcomes_json TEXT NOT NULL, manual_intervention INTEGER NOT NULL DEFAULT 0,
                no_intervention_r DOUBLE PRECISION, outcome_json TEXT NOT NULL,
                resolved_at TEXT NOT NULL, attached_at TEXT NOT NULL,
                UNIQUE(snapshot_id,outcome_checksum)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS research_strategy_decisions(
                id {id_col}, snapshot_id TEXT NOT NULL, signal_id BIGINT NOT NULL,
                strategy_key TEXT NOT NULL, strategy_version TEXT NOT NULL,
                action TEXT NOT NULL, direction TEXT NOT NULL, confidence DOUBLE PRECISION NOT NULL,
                hypothetical_entry DOUBLE PRECISION, hypothetical_stop DOUBLE PRECISION,
                hypothetical_targets_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
                decision_checksum TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(snapshot_id,strategy_key,strategy_version)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS research_signal_rankings(
                id {id_col}, snapshot_id TEXT NOT NULL, signal_id BIGINT NOT NULL,
                rank_version TEXT NOT NULL, diagnostic_score DOUBLE PRECISION NOT NULL,
                components_json TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(snapshot_id,rank_version)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS capability_entitlements(
                id {id_col}, telegram_id BIGINT NOT NULL, capability TEXT NOT NULL,
                enabled INTEGER NOT NULL, source TEXT NOT NULL, expires_at TEXT,
                updated_at TEXT NOT NULL, UNIQUE(telegram_id,capability)
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS paper_order_events(
                id {id_col}, order_id BIGINT NOT NULL, idempotency_key TEXT NOT NULL, telegram_id BIGINT NOT NULL,
                from_status TEXT, to_status TEXT NOT NULL, actor TEXT NOT NULL, reason_code TEXT NOT NULL,
                reason TEXT, created_at TEXT NOT NULL
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
            "profile_name": "TEXT NOT NULL DEFAULT 'STANDARD'",
            "equity_pct": "DOUBLE PRECISION DEFAULT 10",
            "copy_multiplier": "DOUBLE PRECISION DEFAULT 1",
            "max_portfolio_exposure_pct": "DOUBLE PRECISION DEFAULT 70",
            "symbol_policy": "TEXT NOT NULL DEFAULT 'ALL'",
            "symbol_whitelist_json": "TEXT NOT NULL DEFAULT '[]'",
            "symbol_blacklist_json": "TEXT NOT NULL DEFAULT '[]'",
            "timeframe_filters_json": "TEXT NOT NULL DEFAULT '[]'",
            "setup_filters_json": "TEXT NOT NULL DEFAULT '[]'",
            "direction_filters_json": "TEXT NOT NULL DEFAULT '[]'",
            "allow_experimental": "INTEGER NOT NULL DEFAULT 0",
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
        _add_column(conn, "execution_events", "source_event_key", "TEXT")
        for name, definition in {
            "claimed_by": "TEXT",
            "claim_token": "TEXT",
            "claimed_at": "TEXT",
            "lease_expires_at": "TEXT",
            "next_attempt_at": "TEXT",
            "max_attempts": "INTEGER DEFAULT 5",
            "last_retry_at": "TEXT",
            "dead_letter_at": "TEXT",
        }.items():
            _add_column(conn, "copy_execution_journal", name, definition)
        for name, definition in {
            "stop_loss": "DOUBLE PRECISION",
            "risk_amount": "DOUBLE PRECISION",
        }.items():
            _add_column(conn, "paper_execution_orders", name, definition)
        for name, definition in {
            "initial_quantity": "DOUBLE PRECISION",
            "stop_loss": "DOUBLE PRECISION",
            "initial_risk_amount": "DOUBLE PRECISION",
            "remaining_fraction": "DOUBLE PRECISION NOT NULL DEFAULT 1",
            "realized_r": "DOUBLE PRECISION NOT NULL DEFAULT 0",
            "close_reason": "TEXT",
            "last_signal_status": "TEXT",
        }.items():
            _add_column(conn, "paper_execution_positions", name, definition)
        for name, definition in {
            "adapter_environment": "TEXT",
            "adapter_version": "TEXT",
            "account_mode": "TEXT",
            "margin_mode": "TEXT",
            "last_sync_at": "TEXT",
            "sync_status": "TEXT",
            "sync_stage": "TEXT",
            "sync_error_code": "TEXT",
            "sync_error_message": "TEXT",
            "server_time_drift_ms": "BIGINT",
            "capability_snapshot_json": "TEXT",
            "permission_snapshot_json": "TEXT",
            "certification_status": "TEXT",
            "certification_expires_at": "TEXT",
        }.items():
            _add_column(conn, "live_exchange_accounts", name, definition)
        for name, definition in {
            "schema_version": "TEXT", "schema_checksum": "TEXT", "context_version": "TEXT",
            "request_format_version": "TEXT",
        }.items():
            _add_column(conn, "ai_prompt_versions", name, definition)
        for name, definition in {
            "provider_protocol": "TEXT", "schema_version": "TEXT", "schema_checksum": "TEXT",
            "context_version": "TEXT", "request_format_version": "TEXT",
            "requested_output_mode": "TEXT", "effective_output_mode": "TEXT",
            "downgrade_reason": "TEXT", "validation_stage": "TEXT",
            "pricing_version": "TEXT", "cost_status": "TEXT",
            "cached_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
            "reasoning_tokens": "INTEGER NOT NULL DEFAULT 0",
            "provider_request_id": "TEXT", "provider_usage_json": "TEXT",
            "provider_identity_checksum": "TEXT", "provider_endpoint_redacted": "TEXT",
            "capability_snapshot_json": "TEXT", "reasoning_effort": "TEXT",
            "extraction_stage": "TEXT", "extraction_code": "TEXT",
            "raw_envelope_checksum": "TEXT",
            "provider_invoked": "INTEGER NOT NULL DEFAULT 0",
            "legacy_classification": "TEXT",
            "extraction_path": "TEXT",
            "provider_completion_status": "TEXT",
            "provider_incomplete_reason": "TEXT",
            "opportunity_quality": "DOUBLE PRECISION NOT NULL DEFAULT 0",
            "regime_tags_json": "TEXT NOT NULL DEFAULT '[]'",
            "evidence_ranking_json": "TEXT NOT NULL DEFAULT '[]'",
            "uncertainty_explanation": "TEXT",
            "cache_hit": "INTEGER NOT NULL DEFAULT 0",
            "cache_source_decision_id": "TEXT",
            "material_state_checksum": "TEXT",
            "provider_attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            _add_column(conn, "ai_decisions", name, definition)
        for name, definition in {
            "schema_checksum": "TEXT", "reasoning_effort": "TEXT",
            "requested_output_mode": "TEXT", "effective_output_mode": "TEXT",
            "validation_stage": "TEXT", "validation_code": "TEXT",
            "provider_request_id": "TEXT", "returned_model_version": "TEXT",
            "raw_envelope_checksum": "TEXT", "latency_ms": "DOUBLE PRECISION",
            "input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "output_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cached_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
            "reasoning_tokens": "INTEGER NOT NULL DEFAULT 0",
            "estimated_cost_usd": "NUMERIC(18,8) NOT NULL DEFAULT 0",
            "cost_status": "TEXT", "started_at": "TEXT", "completed_at": "TEXT",
            "extraction_path": "TEXT", "provider_completion_status": "TEXT",
            "provider_incomplete_reason": "TEXT",
        }.items():
            _add_column(conn, "ai_provider_certifications", name, definition)
        for name, definition in {
            "identity_checksum": "TEXT", "total_requests": "INTEGER NOT NULL DEFAULT 0",
            "total_failures": "INTEGER NOT NULL DEFAULT 0", "total_retries": "INTEGER NOT NULL DEFAULT 0",
            "last_latency_ms": "DOUBLE PRECISION", "half_opened_at": "TEXT",
        }.items():
            _add_column(conn, "ai_provider_state", name, definition)
        for name, definition in {
            "intervention_type": "TEXT",
            "evaluation_eligible": "INTEGER NOT NULL DEFAULT 1",
        }.items():
            _add_column(conn, "ai_counterfactual_evaluations", name, definition)
        conn.execute("""UPDATE ai_counterfactual_evaluations SET evaluation_eligible=0,
            intervention_type=(SELECT o.intervention_type FROM ai_decision_outcomes o
                WHERE o.decision_id=ai_counterfactual_evaluations.decision_id)
            WHERE EXISTS(SELECT 1 FROM ai_decision_outcomes o
                WHERE o.decision_id=ai_counterfactual_evaluations.decision_id
                  AND o.intervention_type IS NOT NULL)""")
        _add_column(conn, "paper_position_lifecycle_events", "commission_delta", "DOUBLE PRECISION NOT NULL DEFAULT 0")

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
            "CREATE INDEX IF NOT EXISTS idx_copy_profile_events_owner_time ON copy_profile_events(telegram_id,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_paper_positions_owner_status ON paper_positions(telegram_id,status)",
            "CREATE INDEX IF NOT EXISTS idx_paper_positions_signal ON paper_positions(signal_id)",
            "CREATE INDEX IF NOT EXISTS idx_paper_positions_genome ON paper_positions(genome_fingerprint)",
            "CREATE INDEX IF NOT EXISTS idx_execution_events_owner ON execution_events(telegram_id,created_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_events_source_event ON execution_events(source_event_key)",
            "CREATE INDEX IF NOT EXISTS idx_unified_positions_owner_signal ON paper_execution_positions(telegram_id,signal_id)",
            "CREATE INDEX IF NOT EXISTS idx_position_lifecycle_position ON paper_position_lifecycle_events(position_id,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_portfolio_ledger_owner_time ON paper_portfolio_ledger(telegram_id,occurred_at)",
            "CREATE INDEX IF NOT EXISTS idx_portfolio_ledger_position ON paper_portfolio_ledger(position_id)",
            "CREATE INDEX IF NOT EXISTS idx_historical_execution_owner_class ON historical_execution_records(telegram_id,classification)",
            "CREATE INDEX IF NOT EXISTS idx_historical_execution_signal ON historical_execution_records(signal_id)",
            "CREATE INDEX IF NOT EXISTS idx_live_execution_state ON live_executions(state,next_retry_at)",
            "CREATE INDEX IF NOT EXISTS idx_live_execution_owner ON live_executions(telegram_id,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_live_attempt_execution ON live_execution_attempts(execution_id,attempt_number)",
            "CREATE INDEX IF NOT EXISTS idx_live_fill_execution ON live_execution_fills(execution_id,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_bingx_cert_account ON bingx_certification_audits(account_id,started_at)",
            "CREATE INDEX IF NOT EXISTS idx_symbol_rules_expiry ON exchange_symbol_rules_cache(exchange,environment,expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_copy_journal_due ON copy_execution_journal(status,next_attempt_at,id)",
            "CREATE INDEX IF NOT EXISTS idx_copy_journal_expired_lease ON copy_execution_journal(status,lease_expires_at,id)",
            "CREATE INDEX IF NOT EXISTS idx_live_account_sync ON live_exchange_accounts(exchange,sync_status,last_sync_at)",
            "CREATE INDEX IF NOT EXISTS idx_live_readiness_account_time ON live_readiness_audits(account_id,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_decisions_user_time ON ai_decisions(telegram_id,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_decisions_signal ON ai_decisions(signal_id,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_decisions_eval ON ai_decisions(provider,model_version,prompt_version,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_decisions_identity_time ON ai_decisions(provider_identity_checksum,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_decisions_material_cache ON ai_decisions(provider_identity_checksum,material_state_checksum,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_outcomes_unmatched ON ai_decision_outcomes(decision_id,attached_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_cert_identity_expiry ON ai_provider_certifications(identity_checksum,status,expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_observation_identity_time ON ai_observation_events(identity_checksum,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_governance_provider_time ON ai_governance_events(provider,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_provider_state_identity ON ai_provider_state(identity_checksum)",
            "CREATE INDEX IF NOT EXISTS idx_ai_intelligence_identity_time ON ai_decision_intelligence(identity_checksum,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_similarity_source ON ai_decision_similarities(source_decision_id,similarity_score)",
            "CREATE INDEX IF NOT EXISTS idx_ai_counterfactual_identity_regime ON ai_counterfactual_evaluations(identity_checksum,primary_regime)",
            "CREATE INDEX IF NOT EXISTS idx_ai_learning_identity_time ON ai_learning_snapshots(identity_checksum,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_queue_identity_time ON ai_observation_queue_snapshots(identity_checksum,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_request_events_identity_time ON ai_provider_request_events(identity_checksum,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_research_snapshot_owner_time ON research_signal_snapshots(owner_telegram_id,decision_at)",
            "CREATE INDEX IF NOT EXISTS idx_research_snapshot_cohort ON research_signal_snapshots(strategy_key,timeframe,side,primary_regime)",
            "CREATE INDEX IF NOT EXISTS idx_research_outcome_snapshot_version ON research_outcomes(snapshot_id,outcome_version)",
            "CREATE INDEX IF NOT EXISTS idx_research_strategy_key_time ON research_strategy_decisions(strategy_key,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_research_ranking_score ON research_signal_rankings(diagnostic_score,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_capability_entitlements_user ON capability_entitlements(telegram_id,capability)",
            "CREATE INDEX IF NOT EXISTS idx_ai_cost_period ON ai_cost_reconciliations(provider,period_start,period_end)",
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

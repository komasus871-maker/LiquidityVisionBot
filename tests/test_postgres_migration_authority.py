from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import database.database as database
import database.schema_management as schema


def _sqlite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "USE_POSTGRES", False)
    monkeypatch.setattr(database, "REQUIRE_PERSISTENT_DB", False)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DATABASE_NAME", tmp_path / "migration.sqlite3")


class _Cursor:
    def __init__(self, row=None) -> None:
        self.row = row

    def fetchone(self):
        return self.row


class _AdvisoryConnection:
    postgres = True

    def __init__(self, lock: threading.Lock) -> None:
        self.lock = lock
        self.owns_lock = False

    def execute(self, sql, params=()):
        if "pg_try_advisory_lock" in sql:
            self.owns_lock = self.lock.acquire(blocking=False)
            return _Cursor(database.DBRow({"acquired": self.owns_lock}))
        if "pg_advisory_unlock" in sql:
            if self.owns_lock:
                self.lock.release()
                self.owns_lock = False
            return _Cursor(database.DBRow({"unlocked": True}))
        raise AssertionError(f"unexpected advisory-lock SQL: {sql}")

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        if self.owns_lock:
            self.lock.release()
            self.owns_lock = False


def _ready() -> schema.SchemaStatus:
    return schema.SchemaStatus(True, 1, 1, (), "READY")


def test_concurrent_postgres_migrations_are_advisory_lock_serialized(monkeypatch) -> None:
    advisory_lock = threading.Lock()
    monkeypatch.setattr(database, "USE_POSTGRES", True)
    monkeypatch.setattr(database, "connect", lambda: _AdvisoryConnection(advisory_lock))
    monkeypatch.setenv("MIGRATION_LOCK_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("MIGRATION_LOCK_POLL_SECONDS", "0.01")
    active = 0
    max_active = 0
    calls = 0
    state_lock = threading.Lock()

    def ddl() -> None:
        nonlocal active, max_active, calls
        with state_lock:
            calls += 1
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with state_lock:
            active -= 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _: schema.migrate_schema(ddl, ready_check=_ready),
            range(2),
        ))

    assert calls == 2
    assert max_active == 1
    assert all(result.ready for result in results)
    assert not advisory_lock.locked()


def test_postgres_deadlock_retries_with_fresh_locked_attempt(monkeypatch) -> None:
    class DeadlockDetected(RuntimeError):
        pgcode = "40P01"

    advisory_lock = threading.Lock()
    monkeypatch.setattr(database, "USE_POSTGRES", True)
    monkeypatch.setattr(database, "connect", lambda: _AdvisoryConnection(advisory_lock))
    monkeypatch.setenv("MIGRATION_LOCK_POLL_SECONDS", "0.01")
    monkeypatch.setenv("MIGRATION_RETRY_BASE_SECONDS", "0.01")
    attempts = 0

    def ddl() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DeadlockDetected("deadlock detected")

    status = schema.migrate_schema(ddl, ready_check=_ready)

    assert status.ready
    assert attempts == 2
    assert not advisory_lock.locked()


def test_postgres_statement_timeout_is_not_retried(monkeypatch) -> None:
    class StatementTimeout(RuntimeError):
        pgcode = "57014"

    monkeypatch.setattr(database, "USE_POSTGRES", False)
    attempts = 0

    def ddl() -> None:
        nonlocal attempts
        attempts += 1
        raise StatementTimeout("canceling statement due to statement timeout")

    with pytest.raises(StatementTimeout):
        schema.migrate_schema(ddl, ready_check=_ready)

    assert attempts == 1


def test_connect_accepts_bounded_migration_statement_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class RawConnection:
        autocommit = True

        def close(self) -> None:
            pass

    def fake_connect(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return RawConnection()

    monkeypatch.setattr(database, "USE_POSTGRES", True)
    monkeypatch.setattr(database, "DATABASE_URL", "postgresql://example/test")
    monkeypatch.setattr(
        database,
        "psycopg2",
        SimpleNamespace(connect=fake_connect),
        raising=False,
    )

    connection = database.connect(lock_timeout_ms=15_000, statement_timeout_ms=120_000)
    connection.close()

    assert "statement_timeout=120000ms" in str(captured["options"])
    assert "lock_timeout=15000ms" in str(captured["options"])


def test_service_waits_while_postgres_migration_lock_is_active(monkeypatch) -> None:
    advisory_lock = threading.Lock()
    advisory_lock.acquire()
    monkeypatch.setattr(database, "USE_POSTGRES", True)
    monkeypatch.setattr(database, "connect", lambda: _AdvisoryConnection(advisory_lock))
    monkeypatch.setattr(schema, "schema_status", _ready)
    monkeypatch.setenv("SCHEMA_READY_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("SCHEMA_READY_POLL_SECONDS", "0.01")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(schema.wait_for_schema_ready, service_name="web")
        time.sleep(0.05)
        assert not future.done()
        advisory_lock.release()
        status = future.result(timeout=1)

    assert status.ready


def test_service_schema_wait_has_explicit_bounded_timeout(monkeypatch) -> None:
    advisory_lock = threading.Lock()
    advisory_lock.acquire()
    monkeypatch.setattr(database, "USE_POSTGRES", True)
    monkeypatch.setattr(database, "connect", lambda: _AdvisoryConnection(advisory_lock))
    monkeypatch.setenv("SCHEMA_READY_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setenv("SCHEMA_READY_POLL_SECONDS", "0.01")

    try:
        with pytest.raises(schema.SchemaNotReadyError, match=(
            "SCHEMA_NOT_READY service=operational-worker "
            "reason=MIGRATION_IN_PROGRESS timeout_seconds=0.1"
        )):
            schema.wait_for_schema_ready(service_name="operational-worker")
    finally:
        advisory_lock.release()


def test_failed_migration_never_reports_schema_ready(monkeypatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)

    def fail() -> None:
        raise RuntimeError("synthetic migration failure")

    with pytest.raises(RuntimeError, match="synthetic migration failure"):
        schema.migrate_schema(fail)

    status = schema.schema_status()
    assert status.ready is False
    assert status.reason == "MISSING_REQUIRED_TABLES"


def test_services_wait_for_one_migration_then_recover(monkeypatch, tmp_path: Path, caplog) -> None:
    _sqlite(monkeypatch, tmp_path)
    monkeypatch.setenv("SCHEMA_STARTUP_MODE", "wait")
    monkeypatch.setenv("SCHEMA_READY_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("SCHEMA_READY_POLL_SECONDS", "0.01")
    services = ("web", "operational-worker", "forward-worker")

    def delayed_migration() -> schema.SchemaStatus:
        time.sleep(0.05)
        return schema.migrate_schema(database.create_tables)

    with caplog.at_level(logging.INFO):
        with ThreadPoolExecutor(max_workers=4) as pool:
            migration_future = pool.submit(delayed_migration)
            service_futures = [
                pool.submit(schema.initialize_service_database, service_name=name)
                for name in services
            ]
            migration_status = migration_future.result()
            service_statuses = [future.result() for future in service_futures]

    assert migration_status.ready
    assert all(status.ready for status in service_statuses)
    assert "MIGRATION_LOCK_WAIT" in caplog.text
    assert "MIGRATION_LOCK_ACQUIRED" in caplog.text
    assert "MIGRATION_STARTED" in caplog.text
    assert "MIGRATION_COMPLETE" in caplog.text
    assert "MIGRATION_LOCK_RELEASED" in caplog.text
    for service_name in services:
        assert f"SCHEMA_READY service={service_name}" in caplog.text


def test_sequential_migration_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)

    first = schema.migrate_schema(database.create_tables)
    second = schema.migrate_schema(database.create_tables)

    assert first.ready and second.ready
    assert first.applied_version == second.applied_version == 2


def test_worker_default_two_rejects_production_schema_one(monkeypatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)
    monkeypatch.setenv("SCHEMA_VERSION", "1")
    database.create_tables()
    assert schema.schema_status().ready

    monkeypatch.delenv("SCHEMA_VERSION")
    mismatched = schema.schema_status()
    assert mismatched.ready is False
    assert mismatched.expected_version == 2
    assert mismatched.reason == "EXPECTED_VERSION_NOT_APPLIED"

    monkeypatch.setenv("SCHEMA_VERSION", "1")
    assert schema.schema_status().ready


def test_ai_schema_valid_backfill_is_batched_conditional_and_one_time(
    monkeypatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    database.create_tables()
    columns = (
        "decision_id", "idempotency_key", "correlation_id", "signal_id", "symbol",
        "timeframe", "market_timestamp", "market_snapshot_checksum",
        "feature_snapshot_checksum", "provider", "prompt_version", "requested_mode",
        "regime", "direction", "raw_confidence", "uncertainty", "recommended_action",
        "recommended_risk_multiplier", "abstention", "supporting_factors_json",
        "conflicting_factors_json", "invalidation_conditions_json", "explanation",
        "schema_valid", "validation_code", "validation_stage", "provider_invoked",
        "legacy_classification", "created_at",
    )
    cases = (
        ("COMPLETE", 0, "CURRENT_IDENTITY", 1, 0),
        ("PROVIDER_TRANSPORT", 1, "CURRENT_IDENTITY", 1, 0),
        ("COMPLETE", 1, "CURRENT_IDENTITY", 0, 1),
        ("COMPLETE", 1, "CURRENT_IDENTITY", 1, 1),
        ("COMPLETE", 1, "LEGACY_UNSCOPED", 0, 0),
    )
    with database.connect() as conn:
        conn.execute(
            "DELETE FROM product_data_migrations WHERE name=?",
            (database._AI_SCHEMA_VALID_MIGRATION,),
        )
        for index, (stage, invoked, classification, current, _expected) in enumerate(cases, 1):
            values = (
                f"decision-{index}", f"key-{index}", f"correlation-{index}", index,
                "BTCUSDT", "1h", "2026-09-22T00:00:00+00:00", f"market-{index}",
                f"features-{index}", "test", "prompt-v1", "AI_OBSERVE", "RANGING",
                "LONG", 50, 50, "ABSTAIN", 0, 1, "[]", "[]", "[]", "test",
                current, "TEST", stage, invoked, classification,
                "2026-09-22T00:00:00+00:00",
            )
            conn.execute(
                f"INSERT INTO ai_decisions({','.join(columns)}) "
                f"VALUES({','.join('?' for _ in columns)})",
                values,
            )

    monkeypatch.setenv("MIGRATION_AI_SCHEMA_VALID_BATCH_SIZE", "2")
    database.create_tables()

    with database.connect() as conn:
        actual = [
            int(row[0])
            for row in conn.execute(
                "SELECT schema_valid FROM ai_decisions ORDER BY id"
            ).fetchall()
        ]
        marker = conn.execute(
            "SELECT details_json FROM product_data_migrations WHERE name=?",
            (database._AI_SCHEMA_VALID_MIGRATION,),
        ).fetchone()
        second = database._backfill_ai_decision_schema_valid(conn, batch_size=1)

    assert actual == [case[-1] for case in cases]
    assert marker is not None
    assert '"batches": 3' in marker[0]
    assert '"updated": 3' in marker[0]
    assert second == {"applied": False, "batches": 0, "scanned": 0, "updated": 0}


def test_product_migration_uses_separate_bounded_statement_timeout(monkeypatch) -> None:
    import tools.run_product_migrations as product_migrations

    captured: dict[str, int] = {}

    def create_tables(**kwargs) -> None:
        captured.update(kwargs)

    class Historical:
        def run(self, **_kwargs):
            return SimpleNamespace(as_dict=lambda: {})

    class Memory:
        def backfill(self, **_kwargs):
            return {}

    class Retention:
        def run(self):
            return {}

    def authoritative(migration):
        return _ready(), migration()

    monkeypatch.setenv("MIGRATION_DDL_LOCK_TIMEOUT_SECONDS", "17")
    monkeypatch.setenv("MIGRATION_STATEMENT_TIMEOUT_SECONDS", "123")
    monkeypatch.setattr(product_migrations, "create_tables", create_tables)
    monkeypatch.setattr(product_migrations, "HistoricalExecutionMigrationService", Historical)
    monkeypatch.setattr(product_migrations, "TradeMemoryService", Memory)
    monkeypatch.setattr(product_migrations, "OperationalRetentionService", Retention)
    monkeypatch.setattr(product_migrations, "run_authoritative_migration", authoritative)

    product_migrations.run()

    assert captured == {"lock_timeout_ms": 17_000, "statement_timeout_ms": 123_000}


def test_render_and_runtime_sources_have_one_ddl_authority() -> None:
    runtime_sources = {
        "web": Path("bot.py").read_text(encoding="utf-8"),
        "operational": Path("tools/run_operational_worker.py").read_text(encoding="utf-8"),
        "forward": Path("tools/run_forward_microstructure_collector.py").read_text(encoding="utf-8"),
    }
    for source in runtime_sources.values():
        assert "create_tables()" not in source
        assert "initialize_service_database" in source

    blueprint = Path("render.yaml").read_text(encoding="utf-8")
    assert blueprint.count("preDeployCommand: python -m tools.run_product_migrations") == 1
    assert blueprint.count("SCHEMA_STARTUP_MODE") == 3
    service_sections = re.split(r"(?m)^  - type: ", blueprint)[1:]
    assert len(service_sections) == 3
    versions = []
    for service in service_sections:
        match = re.search(
            r'(?m)^      - key: SCHEMA_VERSION\r?\n        value: "([0-9]+)"$',
            service,
        )
        assert match is not None
        versions.append(match.group(1))
    assert versions == ["1", "1", "1"]
    assert re.search(
        r'(?m)^      - key: MIGRATION_STATEMENT_TIMEOUT_SECONDS\r?\n        value: "120"$',
        service_sections[0],
    )
    assert re.search(
        r'(?m)^      - key: MIGRATION_AI_SCHEMA_VALID_BATCH_SIZE\r?\n        value: "500"$',
        service_sections[0],
    )
    assert blueprint.count("healthCheckPath: /health") == 1

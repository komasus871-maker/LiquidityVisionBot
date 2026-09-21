from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
    assert first.applied_version == second.applied_version == 1


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
    assert blueprint.count("healthCheckPath: /health") == 1

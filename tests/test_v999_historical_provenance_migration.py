from __future__ import annotations

from database.database import connect, create_tables
from services.execution_repositories import ExecutionRepository
from services.historical_execution_migration import HistoricalExecutionMigrationService


NOW = "2026-08-07T00:00:00+00:00"


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "v999.db")
    create_tables()
    create_tables()


def seed_signal(conn, signal_id):
    conn.execute(
        """INSERT INTO signals(id,symbol,timeframe,side,status,entry,stop,tp1,tp2,tp3,
               rr,confidence,bull_score,bear_score,recommendation,setup_key,features_json,
               reasons_json,created_at,updated_at)
           VALUES(?,'BTCUSDT','1h','LONG','CLOSED',100,90,110,120,130,2,70,70,30,
                  'BUY','migration','{}','[]',?,?)""", (signal_id, NOW, NOW),
    )


def seed_legacy(conn, row_id, signal_id, *, status="CLOSED", side="LONG", entry=100, qty=1):
    conn.execute(
        """INSERT INTO paper_positions(id,telegram_id,signal_id,symbol,timeframe,side,status,
               entry_price,exit_price,quantity,notional,risk_amount,realized_pnl,realized_r,
               opened_at,closed_at,created_at,updated_at)
           VALUES(?,1,?,'BTCUSDT','1h',?,?,?,110,?,100,10,10,1,?,?,?,?)""",
        (row_id, signal_id, side, status, entry, qty, NOW, NOW, NOW, NOW),
    )


def test_classification_idempotency_and_no_synthetic_economics(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    with connect() as conn:
        for signal_id in (101, 102, 103, 105):
            seed_signal(conn, signal_id)
        seed_legacy(conn, 1, 101)
        seed_legacy(conn, 2, 102)
        seed_legacy(conn, 3, 103, status="REJECTED")
        seed_legacy(conn, 4, 104)  # missing source signal
        seed_legacy(conn, 5, 105, side="SIDEWAYS")
        conn.execute(
            """INSERT INTO paper_execution_positions(
                   position_key,order_id,idempotency_key,telegram_id,signal_id,symbol,timeframe,
                   side,status,quantity,average_entry,last_price,initial_quantity,stop_loss,
                   initial_risk_amount,realized_pnl,realized_r,closed_at,created_at,updated_at)
               VALUES('linked',1,'linked',1,101,'BTCUSDT','1h','LONG','CLOSED',0,100,110,
                      1,90,10,10,1,?,?,?)""", (NOW, NOW, NOW),
        )

    service = HistoricalExecutionMigrationService()
    first = service.run(batch_size=100)
    replay = service.run(batch_size=100)
    assert first.migrated == 5 and first.unresolved == 2 and first.complete
    assert replay.migrated == 0 and replay.skipped == 5
    assert first.classifications == {
        "FULLY_RECONSTRUCTABLE": 1, "PARTIALLY_RECONSTRUCTABLE": 1,
        "LEGACY_ONLY": 1, "AMBIGUOUS": 1, "INVALID": 1,
    }
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM historical_execution_records").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM paper_execution_orders").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_execution_fills").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_position_lifecycle_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_portfolio_ledger").fetchone()[0] == 0


def test_normalized_outcomes_exclude_linked_duplicate_and_unresolved(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    with connect() as conn:
        seed_signal(conn, 201)
        seed_signal(conn, 202)
        seed_legacy(conn, 1, 201)
        seed_legacy(conn, 2, 202)
        conn.execute(
            """INSERT INTO paper_execution_positions(
                   position_key,order_id,idempotency_key,telegram_id,signal_id,symbol,timeframe,
                   side,status,quantity,average_entry,last_price,initial_quantity,stop_loss,
                   initial_risk_amount,realized_pnl,realized_r,closed_at,created_at,updated_at)
               VALUES('linked',1,'linked',1,201,'BTCUSDT','1h','LONG','CLOSED',0,100,110,
                      1,90,10,10,1,?,?,?)""", (NOW, NOW, NOW),
        )
    HistoricalExecutionMigrationService().run(batch_size=100)
    outcomes = ExecutionRepository().closed_outcomes(1)
    assert len(outcomes) == 2
    assert {row["provenance"] for row in outcomes} == {"UNIFIED", "PARTIALLY_RECONSTRUCTABLE"}


def test_bounded_batches_resume_and_schema_is_repeatable(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    with connect() as conn:
        for signal_id in (301, 302, 303):
            seed_signal(conn, signal_id)
            seed_legacy(conn, signal_id - 300, signal_id)
    service = HistoricalExecutionMigrationService()
    first = service.run(batch_size=2)
    second = service.run(batch_size=2)
    third = service.run(batch_size=2)
    assert first.scanned == 2 and not first.complete
    assert second.scanned == 1 and second.complete
    assert third.scanned == 2  # completed scan wraps for checksum verification
    create_tables()
    assert service.latest_report()["classifications"]["PARTIALLY_RECONSTRUCTABLE"] == 3


def test_correlated_lifecycle_mismatch_is_ambiguous_not_deduplicated(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    with connect() as conn:
        seed_signal(conn, 401)
        seed_legacy(conn, 1, 401, status="CLOSED")
        conn.execute(
            """INSERT INTO paper_execution_positions(
                   position_key,order_id,idempotency_key,telegram_id,signal_id,symbol,timeframe,
                   side,status,quantity,average_entry,last_price,initial_quantity,stop_loss,
                   initial_risk_amount,created_at,updated_at)
               VALUES('open-mismatch',1,'open-mismatch',1,401,'BTCUSDT','1h','LONG','OPEN',
                      1,100,100,1,90,10,?,?)""", (NOW, NOW),
        )
    report = HistoricalExecutionMigrationService().run(batch_size=100)
    assert report.classifications == {"AMBIGUOUS": 1}
    with connect() as conn:
        row = conn.execute("SELECT classification,linked_unified_position_id FROM historical_execution_records").fetchone()
    assert row[0] == "AMBIGUOUS" and row[1] is None


def test_upgrade_from_v998_adds_provenance_schema(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    with connect() as conn:
        conn.execute("DROP TABLE historical_execution_records")
        conn.execute("DROP TABLE historical_migration_runs")
    create_tables()
    create_tables()
    with connect() as conn:
        tables = {str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        columns = {str(row[1]) for row in conn.execute(
            "PRAGMA table_info(historical_execution_records)"
        ).fetchall()}
    assert {"historical_execution_records", "historical_migration_runs"} <= tables
    assert {"classification", "provenance_json", "source_checksum", "linked_unified_position_id"} <= columns

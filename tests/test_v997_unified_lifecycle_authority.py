from __future__ import annotations

from database.database import connect, create_tables
from services.copy_trading import CopyTradingService
from services.execution_repositories import ExecutionRepository
from services.portfolio_reconciliation import PortfolioReconciliationService


NOW = "2026-07-24T00:00:00+00:00"


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "v997.db")
    create_tables()


def seed_user_and_signal(user_id: int, signal_id: int, status: str = "ACTIVE") -> dict:
    with connect() as conn:
        conn.execute(
            "INSERT INTO users(telegram_id,username,created_at) VALUES(?,?,?)",
            (user_id, "v997", NOW),
        )
        conn.execute(
            """INSERT INTO signals(
                   id,symbol,timeframe,side,status,entry,current_price,stop,tp1,tp2,tp3,
                   rr,confidence,bull_score,bear_score,recommendation,setup_key,
                   features_json,reasons_json,created_at,updated_at
               ) VALUES(?, 'BTCUSDT','1h','LONG',?,100,100,90,110,120,130,
                        2,80,70,30,'BUY','setup','{}','[]',?,?)""",
            (signal_id, status, NOW, NOW),
        )
    return signal_row(signal_id)


def signal_row(signal_id: int) -> dict:
    with connect() as conn:
        return dict(conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone())


def enable_profile(service: CopyTradingService, user_id: int) -> dict:
    return service.update_profile(
        user_id, enabled=1, max_notional_pct=100, max_heat_r=5, max_positions=5
    )


def update_signal(signal_id: int, status: str, price: float, result: str | None = None) -> dict:
    with connect() as conn:
        conn.execute(
            """UPDATE signals SET status=?,current_price=?,exit_price=?,result=?,updated_at=?
               WHERE id=?""",
            (status, price, price if status in {"TP3", "STOP", "CLOSED"} else None,
             result, NOW, signal_id),
        )
    return signal_row(signal_id)


def test_automatic_open_uses_unified_engine_and_projects_legacy(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = CopyTradingService()
    enable_profile(service, 99701)
    signal = seed_user_and_signal(99701, 701)

    result = service.sync_signal(signal)

    unified = service.execution_repository.position_for_signal(99701, 701)
    with connect() as conn:
        legacy = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE telegram_id=99701 AND signal_id=701"
        ).fetchone())
        journal = dict(conn.execute(
            "SELECT * FROM copy_execution_journal WHERE telegram_id=99701 AND signal_id=701"
        ).fetchone())
    assert result["opened"] == 1
    assert unified["status"] == "OPEN"
    assert legacy["status"] == "OPEN"
    assert journal["status"] == "EXECUTED"
    assert float(unified["initial_risk_amount"]) > 0


def test_signal_lifecycle_is_exactly_once_and_legacy_is_projection(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = CopyTradingService()
    enable_profile(service, 99702)
    service.sync_signal(seed_user_and_signal(99702, 702))

    tp1 = update_signal(702, "TP1", 110)
    first = service.sync_signal(tp1)
    replay = service.sync_signal(tp1)
    unified = service.execution_repository.position_for_signal(99702, 702)
    events = service.execution_repository.lifecycle_events(int(unified["id"]))
    with connect() as conn:
        legacy = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE telegram_id=99702 AND signal_id=702"
        ).fetchone())

    assert first["updated"] == 1
    assert replay["skipped"] == 1
    assert float(unified["remaining_fraction"]) == 0.5
    assert float(legacy["remaining_fraction"]) == 0.5
    assert [event["signal_status"] for event in events] == ["ACTIVE", "TP1"]

    closed = service.sync_signal(update_signal(702, "TP3", 130, "TP3"))
    unified = service.execution_repository.position_for_signal(99702, 702)
    with connect() as conn:
        legacy = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE telegram_id=99702 AND signal_id=702"
        ).fetchone())
    assert closed["closed"] == 1
    assert unified["status"] == "CLOSED"
    assert float(unified["quantity"]) == 0
    assert legacy["status"] == "CLOSED"
    assert float(legacy["realized_pnl"]) == float(unified["realized_pnl"])


def test_panic_closes_unified_authority_before_projection(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = CopyTradingService()
    enable_profile(service, 99703)
    service.sync_signal(seed_user_and_signal(99703, 703))

    assert service.panic(99703) == 1
    unified = service.execution_repository.position_for_signal(99703, 703)
    with connect() as conn:
        legacy = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE telegram_id=99703 AND signal_id=703"
        ).fetchone())
        profile = dict(conn.execute(
            "SELECT * FROM copy_profiles WHERE telegram_id=99703"
        ).fetchone())
    assert unified["status"] == "CLOSED"
    assert unified["close_reason"] == "PANIC_CLOSE"
    assert legacy["status"] == "CLOSED"
    assert profile["enabled"] == 0


def test_reconciliation_does_not_override_live_unified_lifecycle(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = CopyTradingService()
    enable_profile(service, 99704)
    service.sync_signal(seed_user_and_signal(99704, 704))
    with connect() as conn:
        conn.execute("UPDATE signals SET status='TP3' WHERE id=704")

    report = PortfolioReconciliationService().reconcile(99704)
    with connect() as conn:
        legacy = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE telegram_id=99704 AND signal_id=704"
        ).fetchone())

    assert legacy["status"] == "OPEN"
    assert report.lifecycle_mismatch_count == 1
    assert report.status == "UNRESOLVED"


def test_unified_lifecycle_integrity_diagnostics(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = CopyTradingService()
    enable_profile(service, 99705)
    service.sync_signal(seed_user_and_signal(99705, 705))

    assert ExecutionRepository().lifecycle_integrity() == {
        "duplicate_open_positions": 0,
        "positions_missing_lifecycle_metadata": 0,
        "closed_with_quantity": 0,
        "quantity_fraction_mismatch": 0,
    }


def test_lifecycle_event_keys_are_position_scoped_for_multiple_users(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = CopyTradingService()
    seed_user_and_signal(99706, 706)
    with connect() as conn:
        conn.execute(
            "INSERT INTO users(telegram_id,username,created_at) VALUES(?,?,?)",
            (99707, "v997b", NOW),
        )
    enable_profile(service, 99706)
    enable_profile(service, 99707)
    service.sync_signal(signal_row(706))

    result = service.sync_signal(update_signal(706, "TP1", 110))

    first = service.execution_repository.position_for_signal(99706, 706)
    second = service.execution_repository.position_for_signal(99707, 706)
    assert result["updated"] == 2
    assert float(first["remaining_fraction"]) == 0.5
    assert float(second["remaining_fraction"]) == 0.5
    assert len(service.execution_repository.lifecycle_events(int(first["id"]))) == 2
    assert len(service.execution_repository.lifecycle_events(int(second["id"]))) == 2


def test_partial_fill_keeps_quantity_and_remaining_fraction_consistent(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = CopyTradingService()
    enable_profile(service, 99708)
    signal = seed_user_and_signal(99708, 708)
    plan = service.plan_execution(99708, signal)
    order, _ = service.paper_lifecycle.submit(plan)
    service.paper_lifecycle.transition(int(order["id"]), "ACCEPTED")

    result = service.paper_lifecycle.record_fill(
        int(order["id"]),
        quantity=float(plan.quantity) / 2,
        price=float(plan.entry_price),
        fill_key="partial-fill-708",
    )

    assert float(result.position["quantity"]) == float(plan.quantity) / 2
    assert float(result.position["remaining_fraction"]) == 0.5
    assert ExecutionRepository().lifecycle_integrity()["quantity_fraction_mismatch"] == 0


def test_projection_replays_authoritative_event_once_after_crash_window(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = CopyTradingService()
    enable_profile(service, 99709)
    service.sync_signal(seed_user_and_signal(99709, 709))
    unified = service.execution_repository.position_for_signal(99709, 709)
    tp1 = update_signal(709, "TP1", 110)
    event_key = f"position:{unified['id']}:signal:709:TP1"

    lifecycle_result = service.paper_lifecycle.apply_signal_transition(
        int(unified["id"]), signal_status="TP1", price=110, event_key=event_key
    )
    assert lifecycle_result.applied

    assert service.sync_signal(tp1)["skipped"] == 1
    assert service.sync_signal(tp1)["skipped"] == 1
    with connect() as conn:
        legacy = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE telegram_id=99709 AND signal_id=709"
        ).fetchone())
        events = conn.execute(
            "SELECT * FROM execution_events WHERE source_event_key=?", (event_key,)
        ).fetchall()
    assert float(legacy["remaining_fraction"]) == 0.5
    assert len(events) == 1
    assert float(events[0]["realized_pnl_delta"]) == float(lifecycle_result.realized_pnl_delta)


def test_panic_retry_repairs_stale_legacy_projection(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = CopyTradingService()
    enable_profile(service, 99710)
    service.sync_signal(seed_user_and_signal(99710, 710))
    unified = service.execution_repository.position_for_signal(99710, 710)
    service.paper_lifecycle.apply_signal_transition(
        int(unified["id"]), signal_status="PANIC",
        price=100, event_key=f"panic:{unified['id']}", reason="PANIC_CLOSE",
    )

    assert service.panic(99710) == 0
    with connect() as conn:
        legacy = dict(conn.execute(
            "SELECT * FROM paper_positions WHERE telegram_id=99710 AND signal_id=710"
        ).fetchone())
    assert legacy["status"] == "CLOSED"
    assert float(legacy["quantity"]) == 0


def test_stale_signal_transition_cannot_reopen_or_increase_quantity(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = CopyTradingService()
    enable_profile(service, 99711)
    service.sync_signal(seed_user_and_signal(99711, 711))
    service.sync_signal(update_signal(711, "TP2", 120))
    before = service.execution_repository.position_for_signal(99711, 711)

    result = service.paper_lifecycle.apply_signal_transition(
        int(before["id"]), signal_status="TP1", price=110,
        event_key=f"position:{before['id']}:signal:711:late-tp1",
    )

    assert not result.applied
    assert result.event_type == "STALE_NOOP"
    assert float(result.position["quantity"]) == float(before["quantity"])


def test_manual_close_retry_is_idempotent_and_event_backed(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = CopyTradingService()
    enable_profile(service, 99712)
    service.sync_signal(seed_user_and_signal(99712, 712))
    position = service.execution_repository.position_for_signal(99712, 712)
    key = f"operator-close:{position['id']}:one"

    first = service.paper_lifecycle.close_position(
        int(position["id"]), quantity=float(position["quantity"]) / 2,
        exit_price=110, commission_rate=0, event_key=key,
    )
    replay = service.paper_lifecycle.close_position(
        int(position["id"]), quantity=float(position["quantity"]) / 2,
        exit_price=110, commission_rate=0, event_key=key,
    )

    assert float(first["quantity"]) == float(replay["quantity"])
    events = service.execution_repository.lifecycle_events(int(position["id"]))
    assert sum(event["event_key"] == key for event in events) == 1


def test_additive_migration_upgrades_v9969_tables(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "upgrade.db")
    with connect() as conn:
        conn.execute(
            """CREATE TABLE execution_events(
                   id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id BIGINT NOT NULL,
                   signal_id BIGINT, event_type TEXT NOT NULL, price DOUBLE PRECISION,
                   realized_pnl_delta DOUBLE PRECISION DEFAULT 0,
                   details_json TEXT NOT NULL, created_at TEXT NOT NULL
               )"""
        )
        conn.execute(
            """CREATE TABLE paper_execution_positions(
                   id INTEGER PRIMARY KEY AUTOINCREMENT, position_key TEXT NOT NULL UNIQUE,
                   order_id BIGINT NOT NULL UNIQUE, idempotency_key TEXT NOT NULL,
                   telegram_id BIGINT NOT NULL, signal_id BIGINT NOT NULL,
                   symbol TEXT NOT NULL, timeframe TEXT NOT NULL, side TEXT NOT NULL,
                   status TEXT NOT NULL, quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
                   average_entry DOUBLE PRECISION NOT NULL DEFAULT 0,
                   last_price DOUBLE PRECISION, realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
                   unrealized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
                   total_commission DOUBLE PRECISION NOT NULL DEFAULT 0,
                   initial_quantity DOUBLE PRECISION, stop_loss DOUBLE PRECISION,
                   initial_risk_amount DOUBLE PRECISION, opened_at TEXT, closed_at TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
               )"""
        )

    create_tables()
    with connect() as conn:
        event_columns = {row["name"] for row in conn.execute("PRAGMA table_info(execution_events)").fetchall()}
        position_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(paper_execution_positions)").fetchall()
        }
        lifecycle_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_position_lifecycle_events'"
        ).fetchone()
    assert "source_event_key" in event_columns
    assert {"remaining_fraction", "realized_r", "close_reason", "last_signal_status"} <= position_columns
    assert lifecycle_table is not None

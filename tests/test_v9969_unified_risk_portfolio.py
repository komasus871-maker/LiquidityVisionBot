from __future__ import annotations

from database.database import connect, create_tables
from services.copy_execution_engine import CopyExecutionEngine
from services.copy_trading import CopyTradingService
from services.execution_models import CopyExecutionPlan, ExecutionPlanStatus, RiskProfile
from services.execution_repositories import ExecutionRepository
from services.execution_validator import ExecutionValidator
from services.paper_execution_lifecycle import PaperExecutionLifecycle


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "unified-risk.db")
    create_tables()


def plan(user_id: int, signal_id: int, key: str) -> CopyExecutionPlan:
    return CopyExecutionPlan(
        plan_id=key, idempotency_key=key, status=ExecutionPlanStatus.APPROVED,
        code="APPROVED", reason="ok", telegram_id=user_id, signal_id=signal_id,
        exchange_account_id=None, symbol="BTCUSDT", timeframe="1h", side="LONG",
        order_type="MARKET", entry_price=100.0, quantity=2.0, notional=200.0,
        stop_loss=90.0, risk_amount=20.0,
    )


def signal() -> dict:
    return {
        "id": 9999, "symbol": "ETHUSDT", "timeframe": "1h", "side": "LONG",
        "status": "ACTIVE", "entry": 100.0, "current_price": 100.0, "stop": 98.0,
        "tp1": 104.0, "tp2": 106.0, "tp3": 108.0, "confidence": 80.0,
        "preferred_entry_low": 99.0, "preferred_entry_high": 101.0,
    }


def test_unified_only_position_contributes_confirmed_heat(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    assert CopyExecutionEngine().execute(plan(1, 101, "risk-1")).status.value == "EXECUTED"

    state = CopyTradingService()._portfolio_state(1, "ETHUSDT", 30)
    decision = ExecutionValidator().validate(
        signal=signal(), profile=RiskProfile(max_heat_r=1.5), balance=10_000, portfolio=state
    )

    assert state.portfolio_state_resolved
    assert state.current_heat_r == 1.0
    assert state.unified_confirmed_heat_r == 1.0
    assert decision.code == "MAX_HEAT"


def test_partial_close_reduces_unified_heat(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    CopyExecutionEngine().execute(plan(2, 201, "risk-2"))
    position = ExecutionRepository().position_by_idempotency("risk-2")
    PaperExecutionLifecycle().close_position(
        int(position["id"]), quantity=0.5, exit_price=110.0, commission_rate=0
    )

    state = CopyTradingService()._portfolio_state(2, "ETHUSDT", 30)

    assert state.current_heat_r == 0.75
    assert state.unified_confirmed_heat_r == 0.75


def test_legacy_unified_duplicate_does_not_double_count_heat(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    now = "2026-07-24T00:00:00+00:00"
    with connect() as conn:
        conn.execute(
            """INSERT INTO signals(
                   id,symbol,timeframe,side,status,entry,stop,tp1,tp2,tp3,rr,confidence,
                   bull_score,bear_score,recommendation,setup_key,features_json,reasons_json,
                   created_at,updated_at
               ) VALUES(301,'BTCUSDT','1h','LONG','ACTIVE',100,90,110,120,130,2,80,
                        70,30,'BUY','setup','{}','[]',?,?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO paper_positions(
                   telegram_id,signal_id,symbol,timeframe,side,status,entry_price,last_price,
                   stop_price,initial_risk_r,remaining_fraction,created_at,updated_at
               ) VALUES(3,301,'BTCUSDT','1h','LONG','OPEN',100,100,90,1,1,?,?)""",
            (now, now),
        )
    CopyExecutionEngine().execute(plan(3, 301, "risk-3"))

    state = CopyTradingService()._portfolio_state(3, "ETHUSDT", 30)

    assert state.open_positions == 1
    assert state.current_heat_r == 1.0
    assert state.unified_confirmed_heat_r == 0.0


def test_old_unified_row_without_risk_metadata_remains_diagnostics_only(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    now = "2026-07-24T00:00:00+00:00"
    with connect() as conn:
        conn.execute(
            """INSERT INTO paper_execution_positions(
                   position_key,order_id,idempotency_key,telegram_id,signal_id,symbol,timeframe,
                   side,status,quantity,average_entry,last_price,created_at,updated_at
               ) VALUES('old',1,'old',4,401,'BTCUSDT','1h','LONG','OPEN',1,100,100,?,?)""",
            (now, now),
        )

    state = CopyTradingService()._portfolio_state(4, "ETHUSDT", 30)
    assert state.portfolio_state_resolved
    assert state.unified_unresolved_risk_positions == 1
    assert state.current_heat_r == 0.0

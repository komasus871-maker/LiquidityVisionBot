from __future__ import annotations

import sys
import types


class _FakeRouter:
    def message(self, *_args, **_kwargs):
        return lambda func: func


aiogram = types.ModuleType("aiogram")
aiogram.Router = _FakeRouter
filters = types.ModuleType("aiogram.filters")
filters.Command = lambda *_args, **_kwargs: object()
types_module = types.ModuleType("aiogram.types")
types_module.Message = object
sys.modules.setdefault("aiogram", aiogram)
sys.modules.setdefault("aiogram.filters", filters)
sys.modules.setdefault("aiogram.types", types_module)

from database.database import connect, create_tables
from handlers.copy_trading import _status_text
from services.copy_trading import CopyTradingService
from services.execution_models import RiskProfile
from services.execution_validator import ExecutionValidator
from services.portfolio_reconciliation import PortfolioReconciliationService


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "safe_heat.db")
    create_tables()


def seed_user(user_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO users(telegram_id,username,created_at) VALUES(?,?,?)",
            (user_id, "u", "2026-07-24T00:00:00+00:00"),
        )


def seed_signal(signal_id: int, status: str) -> None:
    now = "2026-07-24T00:00:00+00:00"
    with connect() as conn:
        conn.execute(
            """INSERT INTO signals(
                   id,symbol,timeframe,side,status,entry,stop,tp1,tp2,tp3,rr,confidence,
                   bull_score,bear_score,recommendation,setup_key,features_json,reasons_json,
                   created_at,updated_at
               ) VALUES(?, 'BTC','1h','LONG',?,100,90,110,120,130,2,70,70,30,
                        'BUY','setup','{}','[]',?,?)""",
            (signal_id, status, now, now),
        )


def seed_position(user_id: int, signal_id: int, *, risk_r: float = 1.0, remaining: float = 1.0) -> None:
    now = "2026-07-24T00:00:00+00:00"
    with connect() as conn:
        conn.execute(
            """INSERT INTO paper_positions(
                   telegram_id,signal_id,symbol,timeframe,side,status,entry_price,last_price,
                   stop_price,initial_risk_r,remaining_fraction,created_at,updated_at
               ) VALUES(?,?,'BTC','1h','LONG','OPEN',100,100,90,?,?,?,?)""",
            (user_id, signal_id, risk_r, remaining, now, now),
        )


def executable_signal() -> dict:
    return {
        "id": 9001,
        "symbol": "ETH",
        "timeframe": "1h",
        "side": "LONG",
        "status": "ACTIVE",
        "entry": 100.0,
        "current_price": 100.0,
        "stop": 98.0,
        "tp1": 104.0,
        "tp2": 106.0,
        "tp3": 108.0,
        "preferred_entry_low": 99.0,
        "preferred_entry_high": 101.0,
        "confidence": 80.0,
    }


def test_terminal_legacy_position_is_closed_and_no_longer_causes_max_heat(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_user(99670)
    seed_signal(1, "CLOSED")
    seed_position(99670, 1)

    service = CopyTradingService()
    state = service._portfolio_state(99670, "ETH", 30)
    decision = ExecutionValidator().validate(
        signal=executable_signal(),
        profile=RiskProfile(max_heat_r=1.0, max_notional_pct=100.0),
        balance=10_000,
        portfolio=state,
    )

    assert state.portfolio_state_resolved
    assert state.open_positions == 0
    assert state.current_heat_r == 0.0
    assert decision.code != "MAX_HEAT"
    assert decision.allowed


def test_active_legacy_position_remains_confirmed_and_max_heat_still_applies(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_user(99671)
    seed_signal(2, "ACTIVE")
    seed_position(99671, 2, risk_r=1.0)

    service = CopyTradingService()
    report = service.reconciliation.reconcile(99671)
    state = service._portfolio_state(99671, "ETH", 30)
    decision = ExecutionValidator().validate(
        signal=executable_signal(),
        profile=RiskProfile(max_heat_r=1.5),
        balance=10_000,
        portfolio=state,
    )

    assert report.confirmed_active_legacy_count == 1
    assert report.unresolved_legacy_count == 0
    assert report.confirmed_active_heat_r == 1.0
    assert state.current_heat_r == 1.0
    assert decision.code == "MAX_HEAT"


def test_missing_signal_is_unresolved_and_uses_distinct_fail_closed_code(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_user(99672)
    seed_position(99672, 999, risk_r=1.25, remaining=0.5)

    service = CopyTradingService()
    report = service.reconciliation.reconcile(99672)
    state = service._portfolio_state(99672, "ETH", 30)
    decision = ExecutionValidator().validate(
        signal=executable_signal(),
        profile=RiskProfile(max_heat_r=20.0),
        balance=10_000,
        portfolio=state,
    )

    assert report.unresolved_legacy_count == 1
    assert report.unresolved_heat_r == 0.625
    assert report.stale_legacy_closed_count == 0
    assert not state.portfolio_state_resolved
    assert decision.code == "PORTFOLIO_STATE_UNRESOLVED"
    assert "1 legacy position" in decision.reason
    assert "0.62R" in decision.reason


def test_reconciliation_is_idempotent_after_terminal_close(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_user(99673)
    seed_signal(3, "TP3")
    seed_position(99673, 3)

    service = PortfolioReconciliationService()
    first = service.reconcile(99673)
    with connect() as conn:
        row_before = dict(conn.execute("SELECT status,close_reason,closed_at,updated_at FROM paper_positions").fetchone())
    second = service.reconcile(99673)
    with connect() as conn:
        row_after = dict(conn.execute("SELECT status,close_reason,closed_at,updated_at FROM paper_positions").fetchone())

    assert first.stale_legacy_closed_count == 1
    assert second.stale_legacy_closed_count == 0
    assert row_before == row_after


def test_empty_legacy_portfolio_is_resolved_with_zero_heat(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_user(99674)

    state = CopyTradingService()._portfolio_state(99674, "BTC", 30)

    assert state.portfolio_state_resolved
    assert state.open_positions == 0
    assert state.current_heat_r == 0.0
    assert state.unresolved_legacy_positions == 0
    assert state.heat_source == "EMPTY"


def test_copy_stats_formatter_exposes_reconciliation_diagnostics():
    profile = {
        "enabled": 1, "paper_balance": 10_000, "sizing_mode": "FIXED_USDT",
        "risk_pct": 0.5, "fixed_usdt": 100, "leverage": 5, "auto_copy": 1,
        "max_positions": 5, "max_heat_r": 5, "daily_loss_pct": 2,
        "max_slippage_pct": 0.25, "min_confidence": 55, "max_notional_pct": 35,
        "symbol_cooldown_min": 30,
    }
    stats = {
        "open_count": 2, "closed_count": 0, "rejected_count": 1,
        "equity": 10_000, "reconciliation_confirmed_active_legacy_count": 1,
        "reconciliation_unified_open_count": 0, "reconciliation_unresolved_legacy_count": 1,
        "reconciliation_unresolved_heat_r": 0.75,
        "reconciliation_confirmed_active_heat_r": 1.0,
        "reconciliation_heat_source": "LEGACY_CONFIRMED+UNRESOLVED",
        "reconciliation_stale_legacy_closed_count": 0,
        "reconciliation_status": "UNRESOLVED",
        "reconciliation_mismatch_detected": True,
    }

    text = _status_text(profile, stats)

    assert "Confirmed legacy active: 1" in text
    assert "Unresolved legacy: 1 (0.75R)" in text
    assert "Heat source: <b>LEGACY_CONFIRMED+UNRESOLVED</b>" in text
    assert "Portfolio state: <b>UNRESOLVED</b>" in text
    assert "Mismatch: <b>YES</b>" in text

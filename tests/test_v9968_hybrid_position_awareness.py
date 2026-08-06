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
from services.execution_repositories import ExecutionRepository
from services.execution_validator import ExecutionValidator


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "hybrid.db")
    create_tables()


def seed_signal(signal_id: int, status: str = "ACTIVE", symbol: str = "BTCUSDT") -> None:
    now = "2026-07-24T00:00:00+00:00"
    with connect() as conn:
        conn.execute(
            """INSERT INTO signals(
                   id,symbol,timeframe,side,status,entry,stop,tp1,tp2,tp3,rr,confidence,
                   bull_score,bear_score,recommendation,setup_key,features_json,reasons_json,
                   created_at,updated_at
               ) VALUES(?,?, '1h','LONG',?,100,90,110,120,130,2,80,70,30,
                        'BUY','setup','{}','[]',?,?)""",
            (signal_id, symbol, status, now, now),
        )


def seed_legacy(user_id: int, signal_id: int, symbol: str = "BTCUSDT", risk_r: float = 1.0) -> None:
    now = "2026-07-24T00:00:00+00:00"
    with connect() as conn:
        conn.execute(
            """INSERT INTO paper_positions(
                   telegram_id,signal_id,symbol,timeframe,side,status,entry_price,last_price,
                   stop_price,initial_risk_r,remaining_fraction,created_at,updated_at
               ) VALUES(?,?,?,'1h','LONG','OPEN',100,100,90,?,1,?,?)""",
            (user_id, signal_id, symbol, risk_r, now, now),
        )


def seed_unified(
    user_id: int,
    signal_id: int,
    *,
    position_id: int,
    symbol: str = "BTCUSDT",
    side: str = "LONG",
    status: str = "OPEN",
    quantity: float = 2.0,
    average_entry: float = 100.0,
    last_price: float | None = 110.0,
    realized_pnl: float = 0.0,
    unrealized_pnl: float = 0.0,
    commission: float = 0.0,
) -> None:
    now = "2026-07-24T00:00:00+00:00"
    with connect() as conn:
        conn.execute(
            """INSERT INTO paper_execution_positions(
                   position_key,order_id,idempotency_key,telegram_id,signal_id,symbol,timeframe,side,status,
                   quantity,average_entry,last_price,realized_pnl,unrealized_pnl,total_commission,
                   opened_at,created_at,updated_at
               ) VALUES(?,?,?,?,?,?, '1h',?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"position-{position_id}", position_id, f"idem-{position_id}", user_id, signal_id,
                symbol, side, status, quantity, average_entry, last_price, realized_pnl,
                unrealized_pnl, commission, now, now, now,
            ),
        )


def signal(symbol: str = "BTCUSDT") -> dict:
    return {
        "id": 9900,
        "symbol": symbol,
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


def test_unified_only_symbol_blocks_duplicate_without_affecting_heat(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_unified(1, 101, position_id=1, symbol=" btcusdt ")

    state = CopyTradingService()._portfolio_state(1, "BTCUSDT", 30)
    decision = ExecutionValidator().validate(
        signal=signal(), profile=RiskProfile(max_positions=3, max_heat_r=2.5), balance=10_000, portfolio=state
    )

    assert state.symbol_is_open
    assert state.open_positions == 1
    assert state.current_heat_r == 0.0
    assert not state.portfolio_state_resolved
    assert state.legacy_open_positions == 0
    assert state.unified_open_positions == 1
    assert decision.code == "PORTFOLIO_STATE_UNRESOLVED"


def test_legacy_only_active_position_keeps_existing_behavior(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_signal(201)
    seed_legacy(2, 201, risk_r=1.25)

    state = CopyTradingService()._portfolio_state(2, "BTCUSDT", 30)

    assert state.symbol_is_open
    assert state.open_positions == 1
    assert state.current_heat_r == 1.25
    assert state.legacy_open_positions == 1
    assert state.unified_open_positions == 0


def test_same_signal_in_both_sources_is_deduplicated(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_signal(301)
    seed_legacy(3, 301, risk_r=1.0)
    seed_unified(3, 301, position_id=301)

    state = CopyTradingService()._portfolio_state(3, "BTCUSDT", 30)

    assert state.open_positions == 1
    assert state.deduplicated_open_positions == 1
    assert state.legacy_open_positions == 1
    assert state.unified_open_positions == 1
    assert state.current_heat_r == 1.0
    assert state.symbol_is_open


def test_different_signals_are_not_deduplicated_even_for_same_symbol(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_signal(401)
    seed_legacy(4, 401)
    seed_unified(4, 402, position_id=402, symbol="BTCUSDT")

    state = CopyTradingService()._portfolio_state(4, "BTCUSDT", 30)

    assert state.open_positions == 2
    assert state.deduplicated_open_positions == 2


def test_unresolved_legacy_remains_fail_closed_with_unified_position(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_legacy(5, 501)
    seed_unified(5, 502, position_id=502)

    state = CopyTradingService()._portfolio_state(5, "BTCUSDT", 30)
    decision = ExecutionValidator().validate(
        signal=signal(), profile=RiskProfile(max_positions=1, max_heat_r=0.5), balance=10_000, portfolio=state
    )

    assert not state.portfolio_state_resolved
    assert decision.code == "PORTFOLIO_STATE_UNRESOLVED"


def test_unified_open_state_uses_only_supported_lifecycle_statuses(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    statuses = ["OPEN", "PARTIALLY_FILLED", "PARTIALLY_CLOSED", "CLOSED", "CANCELLED", "FAILED"]
    for index, status in enumerate(statuses, start=1):
        seed_unified(6, 600 + index, position_id=600 + index, status=status, symbol=f"S{index}")

    state = ExecutionRepository().unified_open_state(6)

    assert state.open_count == 3
    assert state.symbols == ("S1", "S2", "S3")


def test_unified_exposure_aggregation_and_price_fallback(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_unified(
        7, 701, position_id=701, symbol="BTC", side="LONG", quantity=2,
        average_entry=100, last_price=110, realized_pnl=4, unrealized_pnl=20, commission=1,
    )
    seed_unified(
        7, 702, position_id=702, symbol="ETH", side="SHORT", quantity=3,
        average_entry=50, last_price=None, realized_pnl=-2, unrealized_pnl=6, commission=0.5,
    )

    state = ExecutionRepository().unified_open_state(7)

    assert state.open_count == 2
    assert state.gross_notional == 370.0
    assert state.net_notional == 70.0
    assert state.unrealized_pnl == 26.0
    assert state.realized_pnl == 2.0
    assert state.total_commission == 1.5
    assert state.long_count == 1
    assert state.short_count == 1
    assert state.signal_ids == frozenset({701, 702})
    assert state.idempotency_keys == frozenset({"idem-701", "idem-702"})


def test_copy_stats_formatter_shows_hybrid_diagnostics():
    profile = {
        "enabled": 1, "paper_balance": 10_000, "sizing_mode": "FIXED_USDT",
        "risk_pct": 0.5, "fixed_usdt": 100, "leverage": 5, "auto_copy": 1,
        "max_positions": 5, "max_heat_r": 5, "daily_loss_pct": 2,
        "max_slippage_pct": 0.25, "min_confidence": 55, "max_notional_pct": 35,
        "symbol_cooldown_min": 30,
    }
    stats = {
        "open_count": 1, "closed_count": 0, "rejected_count": 0, "equity": 10_000,
        "legacy_confirmed_open": 1, "unified_open_positions": 2, "hybrid_open_positions": 2,
        "position_state_source": "HYBRID_LEGACY_UNIFIED", "unified_symbols": ("BTC", "ETH"),
        "unified_gross_notional": 500, "unified_net_notional": 100,
        "unified_unrealized_pnl": 12.5, "unified_commission": 1.25,
        "reconciliation_unresolved_legacy_count": 0, "reconciliation_unresolved_heat_r": 0,
        "reconciliation_confirmed_active_heat_r": 1, "reconciliation_heat_source": "LEGACY_CONFIRMED",
        "reconciliation_stale_legacy_closed_count": 0, "reconciliation_status": "MATCHED",
        "reconciliation_mismatch_detected": False,
    }

    text = _status_text(profile, stats)

    assert "Legacy confirmed open: 1" in text
    assert "Unified open: 2" in text
    assert "Hybrid open: 2" in text
    assert "Accounting authority: <b>LEGACY</b>" in text
    assert "Unified symbols: BTC, ETH" in text
    assert "Legacy values are shadow/rollback diagnostics" in text

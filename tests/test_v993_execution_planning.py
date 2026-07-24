from __future__ import annotations

from services.copy_execution_planner import CopyExecutionPlanner
from services.execution_models import (
    ExecutionPlanStatus,
    PortfolioState,
    PositionSizingMode,
    RiskProfile,
)
from version import APP_VERSION, RELEASE_NAME


def _signal() -> dict:
    return {
        "id": 993,
        "symbol": "BTCUSDT",
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


def test_release_identity() -> None:
    assert APP_VERSION == "9.9.3"
    assert RELEASE_NAME == "Copy Execution Planning Layer"


def test_planner_builds_complete_approved_plan_without_side_effects() -> None:
    plan = CopyExecutionPlanner().build(
        telegram_id=42,
        signal=_signal(),
        profile=RiskProfile(
            sizing_mode=PositionSizingMode.FIXED_USDT,
            fixed_usdt=250.0,
            leverage=5,
            max_notional_pct=100.0,
        ),
        balance=10_000,
        portfolio=PortfolioState(),
        exchange_account_id=7,
    )
    assert plan.status is ExecutionPlanStatus.APPROVED
    assert plan.approved
    assert plan.quantity == 2.5
    assert plan.notional == 250.0
    assert plan.risk_amount == 5.0
    assert plan.leverage == 5
    assert plan.stop_loss == 98.0
    assert plan.take_profits == (104.0, 106.0, 108.0)
    assert plan.exchange_account_id == 7
    assert plan.profile_snapshot is not None
    assert plan.profile_snapshot["sizing_mode"] == "FIXED_USDT"


def test_planner_is_deterministic_for_same_user_signal_and_account() -> None:
    planner = CopyExecutionPlanner()
    kwargs = dict(
        telegram_id=42,
        signal=_signal(),
        profile=RiskProfile(max_notional_pct=100.0),
        balance=10_000,
        exchange_account_id=9,
    )
    first = planner.build(**kwargs)
    second = planner.build(**kwargs)
    assert first.plan_id == second.plan_id
    assert first.idempotency_key == second.idempotency_key


def test_planner_returns_formal_rejection_from_shared_validator() -> None:
    plan = CopyExecutionPlanner().build(
        telegram_id=42,
        signal=_signal(),
        profile=RiskProfile(max_positions=1),
        balance=10_000,
        portfolio=PortfolioState(open_positions=1),
    )
    assert plan.status is ExecutionPlanStatus.REJECTED
    assert not plan.approved
    assert plan.code == "MAX_POSITIONS"
    assert plan.quantity is None


def test_future_auto_executor_can_fail_closed_when_auto_copy_is_disabled() -> None:
    plan = CopyExecutionPlanner().build(
        telegram_id=42,
        signal=_signal(),
        profile=RiskProfile(auto_copy=False),
        balance=10_000,
        require_auto_copy=True,
    )
    assert plan.status is ExecutionPlanStatus.REJECTED
    assert plan.code == "AUTO_COPY_DISABLED"

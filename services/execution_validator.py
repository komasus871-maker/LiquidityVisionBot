from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.data_integrity import DataIntegrityEngine
from services.execution_models import ExecutionDecision, RiskProfile
from services.position_sizer import PositionSizer


class ExecutionValidator:
    """Fail-closed gateway shared by paper and future live executors."""

    def __init__(self) -> None:
        self.integrity = DataIntegrityEngine(max_activation_deviation_pct=1.5, max_stale_minutes=5)
        self.sizer = PositionSizer()

    def validate(
        self,
        *,
        signal: dict[str, Any],
        profile: RiskProfile,
        balance: float,
        open_positions: int,
        current_heat_r: float,
        market_price: float | None = None,
    ) -> ExecutionDecision:
        if str(signal.get("status")) not in {"ACTIVE", "TP1", "TP2"}:
            return ExecutionDecision(False, "SIGNAL_NOT_ACTIVE", "Signal is not executable")
        if open_positions >= profile.max_positions:
            return ExecutionDecision(False, "MAX_POSITIONS", "Maximum open positions reached")
        if current_heat_r + 1.0 > profile.max_heat_r:
            return ExecutionDecision(False, "MAX_HEAT", "Portfolio heat limit exceeded")
        plan = {
            "direction": signal.get("side"), "entry": signal.get("entry"), "stop": signal.get("stop"),
            "tp1": signal.get("tp1"), "tp2": signal.get("tp2"), "tp3": signal.get("tp3"),
            "preferred_entry_low": signal.get("preferred_entry_low"),
            "preferred_entry_high": signal.get("preferred_entry_high"),
        }
        integrity = self.integrity.validate_plan(plan)
        if not integrity.valid:
            return ExecutionDecision(False, integrity.code, integrity.reason)
        try:
            price = float(market_price if market_price is not None else signal.get("current_price") or signal.get("entry"))
            activation = self.integrity.validate_activation(signal, price)
            if not activation.valid:
                return ExecutionDecision(False, activation.code, activation.reason)
            size = self.sizer.calculate(
                balance=balance,
                risk_pct=profile.risk_pct,
                entry=price,
                stop=float(signal["stop"]),
            )
        except (TypeError, ValueError, KeyError) as exc:
            return ExecutionDecision(False, "SIZING_FAILED", str(exc))
        activated_at = signal.get("activated_at")
        if activated_at:
            try:
                timestamp = datetime.fromisoformat(str(activated_at).replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if timestamp > datetime.now(timezone.utc):
                    return ExecutionDecision(False, "FUTURE_TIMESTAMP", "Activation timestamp is in the future")
            except ValueError:
                return ExecutionDecision(False, "INVALID_TIMESTAMP", "Activation timestamp is invalid")
        return ExecutionDecision(True, "APPROVED", "Execution checks passed", size=size)

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.data_integrity import DataIntegrityEngine
from services.execution_models import ExecutionDecision, PortfolioState, RiskProfile
from services.position_sizer import PositionSizer
from services.copy_training import CopyTrainingPolicy


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
        open_positions: int = 0,
        current_heat_r: float = 0.0,
        market_price: float | None = None,
        portfolio: PortfolioState | None = None,
        training_policy: CopyTrainingPolicy | None = None,
    ) -> ExecutionDecision:
        state = portfolio or PortfolioState(open_positions=open_positions, current_heat_r=current_heat_r)
        policy = training_policy or CopyTrainingPolicy()
        if policy.blocked:
            return ExecutionDecision(False, policy.code, policy.reason, training_sample_size=policy.sample_size)
        if str(signal.get("status")) not in {"ACTIVE", "TP1", "TP2"}:
            return ExecutionDecision(False, "SIGNAL_NOT_ACTIVE", "Signal is not executable")
        if state.open_positions >= profile.max_positions:
            return ExecutionDecision(False, "MAX_POSITIONS", "Maximum open positions reached")
        if state.current_heat_r + 1.0 > profile.max_heat_r:
            return ExecutionDecision(False, "MAX_HEAT", "Portfolio heat limit exceeded")
        if state.symbol_is_open:
            return ExecutionDecision(False, "SYMBOL_ALREADY_OPEN", "An open copied position already exists for this symbol")
        if state.symbol_in_cooldown:
            return ExecutionDecision(False, "SYMBOL_COOLDOWN", "Symbol is still in post-trade cooldown")
        daily_limit = max(0.0, balance * profile.daily_loss_pct / 100.0)
        if state.daily_realized_pnl <= -daily_limit and daily_limit > 0:
            return ExecutionDecision(False, "DAILY_LOSS_LIMIT", "Daily copy-trading loss limit reached")
        confidence_raw = signal.get("dynamic_confidence") if signal.get("dynamic_confidence") is not None else signal.get("confidence")
        confidence = float(100.0 if confidence_raw is None else confidence_raw)
        adaptive_min_confidence = max(0.0, min(100.0, profile.min_confidence - policy.confidence_adjustment))
        if confidence < adaptive_min_confidence:
            return ExecutionDecision(
                False, "LOW_CONFIDENCE",
                f"Signal confidence {confidence:.1f} is below adaptive threshold {adaptive_min_confidence:.1f}",
                training_sample_size=policy.sample_size,
            )

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
            planned_entry = float(signal["entry"])
            price = float(market_price if market_price is not None else signal.get("current_price") or planned_entry)
            slippage_pct = abs(price - planned_entry) / planned_entry * 100.0 if planned_entry else 100.0
            if slippage_pct > profile.max_slippage_pct:
                return ExecutionDecision(False, "MAX_SLIPPAGE", f"Expected slippage {slippage_pct:.3f}% exceeds {profile.max_slippage_pct:.3f}%", expected_slippage_pct=slippage_pct)
            activation = self.integrity.validate_activation(signal, price)
            if not activation.valid:
                return ExecutionDecision(False, activation.code, activation.reason)
            size = self.sizer.calculate(balance=balance, risk_pct=profile.risk_pct, entry=price, stop=float(signal["stop"]))
            if policy.risk_multiplier != 1.0:
                size = type(size)(
                    quantity=size.quantity * policy.risk_multiplier,
                    notional=size.notional * policy.risk_multiplier,
                    risk_amount=size.risk_amount * policy.risk_multiplier,
                    stop_distance_pct=size.stop_distance_pct,
                )
            max_notional = balance * profile.max_notional_pct / 100.0
            if size.notional > max_notional > 0:
                scale = max_notional / size.notional
                size = type(size)(
                    quantity=size.quantity * scale,
                    notional=max_notional,
                    risk_amount=size.risk_amount * scale,
                    stop_distance_pct=size.stop_distance_pct,
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
        return ExecutionDecision(
            True, "APPROVED", "Execution checks passed", size=size,
            expected_slippage_pct=slippage_pct, risk_multiplier=policy.risk_multiplier,
            training_sample_size=policy.sample_size,
        )

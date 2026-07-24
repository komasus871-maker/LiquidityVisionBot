from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from services.copy_training import CopyTrainingPolicy
from services.execution_models import (
    CopyExecutionPlan,
    ExecutionPlanStatus,
    PortfolioState,
    RiskProfile,
)
from services.execution_validator import ExecutionValidator


class CopyExecutionPlanner:
    """Builds a deterministic, side-effect-free execution plan.

    The planner is the contract between signal discovery and any future paper/demo/live
    executor. It never places orders and therefore remains safe to call for previews,
    queue preparation, auditing, and idempotency checks.
    """

    def __init__(self, validator: ExecutionValidator | None = None) -> None:
        self.validator = validator or ExecutionValidator()

    def build(
        self,
        *,
        telegram_id: int,
        signal: dict[str, Any],
        profile: RiskProfile,
        balance: float,
        portfolio: PortfolioState | None = None,
        training_policy: CopyTrainingPolicy | None = None,
        market_price: float | None = None,
        exchange_account_id: int | None = None,
        require_auto_copy: bool = False,
    ) -> CopyExecutionPlan:
        signal_id = int(signal.get("id") or 0)
        symbol = str(signal.get("symbol") or "").upper()
        side = str(signal.get("side") or "").upper()
        timeframe = str(signal.get("timeframe") or "")
        entry = self._optional_float(
            market_price if market_price is not None else signal.get("current_price") or signal.get("entry")
        )
        profile_snapshot = self._profile_snapshot(profile)
        idempotency_key = self._idempotency_key(
            telegram_id=telegram_id,
            signal_id=signal_id,
            exchange_account_id=exchange_account_id,
            symbol=symbol,
            side=side,
        )

        if require_auto_copy and not profile.auto_copy:
            return CopyExecutionPlan(
                plan_id=idempotency_key,
                idempotency_key=idempotency_key,
                status=ExecutionPlanStatus.REJECTED,
                code="AUTO_COPY_DISABLED",
                reason="Automatic copy execution is disabled in the user profile",
                telegram_id=telegram_id,
                signal_id=signal_id,
                exchange_account_id=exchange_account_id,
                symbol=symbol,
                timeframe=timeframe,
                side=side,
                order_type="MARKET",
                entry_price=entry,
                stop_loss=self._optional_float(signal.get("stop")),
                take_profits=self._take_profits(signal),
                leverage=profile.leverage,
                sizing_mode=profile.sizing_mode.value,
                profile_snapshot=profile_snapshot,
            )

        decision = self.validator.validate(
            signal=signal,
            profile=profile,
            balance=max(0.0, float(balance)),
            portfolio=portfolio,
            training_policy=training_policy,
            market_price=market_price,
        )
        size = decision.size
        approved = decision.allowed and size is not None
        return CopyExecutionPlan(
            plan_id=idempotency_key,
            idempotency_key=idempotency_key,
            status=ExecutionPlanStatus.APPROVED if approved else ExecutionPlanStatus.REJECTED,
            code=decision.code,
            reason=decision.reason,
            telegram_id=telegram_id,
            signal_id=signal_id,
            exchange_account_id=exchange_account_id,
            symbol=symbol,
            timeframe=timeframe,
            side=side,
            order_type="MARKET",
            entry_price=entry,
            quantity=size.quantity if size else None,
            notional=size.notional if size else None,
            leverage=profile.leverage,
            stop_loss=self._optional_float(signal.get("stop")),
            take_profits=self._take_profits(signal),
            risk_amount=size.risk_amount if size else None,
            stop_distance_pct=size.stop_distance_pct if size else None,
            sizing_mode=profile.sizing_mode.value,
            expected_slippage_pct=decision.expected_slippage_pct,
            risk_multiplier=decision.risk_multiplier,
            training_sample_size=decision.training_sample_size,
            profile_snapshot=profile_snapshot,
        )

    @staticmethod
    def _profile_snapshot(profile: RiskProfile) -> dict[str, Any]:
        snapshot = asdict(profile)
        snapshot["sizing_mode"] = profile.sizing_mode.value
        return snapshot

    @staticmethod
    def _take_profits(signal: dict[str, Any]) -> tuple[float, ...]:
        values: list[float] = []
        for key in ("tp1", "tp2", "tp3"):
            value = CopyExecutionPlanner._optional_float(signal.get(key))
            if value is not None:
                values.append(value)
        return tuple(values)

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _idempotency_key(
        *, telegram_id: int, signal_id: int, exchange_account_id: int | None, symbol: str, side: str
    ) -> str:
        payload = {
            "telegram_id": int(telegram_id),
            "signal_id": int(signal_id),
            "exchange_account_id": exchange_account_id,
            "symbol": symbol,
            "side": side,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
        return f"copy-plan-{digest}"

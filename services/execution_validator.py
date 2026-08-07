from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from services.data_integrity import DataIntegrityEngine
from services.execution_models import ExecutionDecision, PortfolioState, PositionSize, PositionSizingMode, RiskProfile
from services.position_sizer import PositionSizer
from services.copy_training import CopyTrainingPolicy
from services.copy_controls import normalize_copy_symbol, normalize_setup, normalize_timeframe


class ExecutionValidator:
    """Fail-closed gateway for PAPER planning and separately gated live preflight."""

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
        symbol = normalize_copy_symbol(signal.get("symbol"))
        whitelist = {normalize_copy_symbol(value) for value in profile.symbol_whitelist}
        blacklist = {normalize_copy_symbol(value) for value in profile.symbol_blacklist}
        if symbol in blacklist:
            return ExecutionDecision(False, "SYMBOL_BLACKLISTED", "Symbol is blocked by the user blacklist")
        if profile.symbol_policy == "WHITELIST" and symbol not in whitelist:
            return ExecutionDecision(False, "SYMBOL_NOT_WHITELISTED", "Symbol is not in the user whitelist")
        timeframe = normalize_timeframe(signal.get("timeframe"))
        if profile.timeframe_filters and timeframe not in {
                normalize_timeframe(value) for value in profile.timeframe_filters}:
            return ExecutionDecision(False, "TIMEFRAME_FILTERED", "Signal timeframe is not enabled")
        setup = normalize_setup(signal.get("setup_key"))
        if profile.setup_filters and setup not in {normalize_setup(value) for value in profile.setup_filters}:
            return ExecutionDecision(False, "SETUP_FILTERED", "Signal setup family is not enabled")
        direction = str(signal.get("side") or "").upper()
        if profile.direction_filters and direction not in {str(value).upper() for value in profile.direction_filters}:
            return ExecutionDecision(False, "DIRECTION_FILTERED", "Signal direction is not enabled")
        try:
            feature_payload = json.loads(signal.get("features_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            feature_payload = {}
        experimental = bool(signal.get("experimental") or signal.get("is_experimental") or
                            str(signal.get("strategy_status") or "").upper() == "EXPERIMENTAL" or
                            isinstance(feature_payload, dict) and feature_payload.get("experimental") is True)
        if experimental and not profile.allow_experimental:
            return ExecutionDecision(False, "EXPERIMENTAL_STRATEGY_BLOCKED",
                                     "Experimental strategies are disabled")
        if not state.portfolio_state_resolved:
            return ExecutionDecision(
                False,
                "PORTFOLIO_STATE_UNRESOLVED",
                "Portfolio state is unresolved: "
                f"{state.unresolved_legacy_positions} legacy position(s) or unified position(s) "
                "with unresolved risk, "
                f"{state.unresolved_heat_r:.2f}R unresolved heat, "
                f"reconciliation={state.reconciliation_status}",
            )
        if state.open_positions >= profile.max_positions:
            return ExecutionDecision(False, "MAX_POSITIONS", "Maximum open positions reached")
        if state.current_heat_r + 1.0 > profile.max_heat_r:
            return ExecutionDecision(False, "MAX_HEAT", "Portfolio heat limit exceeded")
        if state.symbol_is_open:
            return ExecutionDecision(False, "SYMBOL_ALREADY_OPEN", "An open copied position already exists for this symbol")
        if state.symbol_in_cooldown:
            return ExecutionDecision(False, "SYMBOL_COOLDOWN", "Symbol is still in post-trade cooldown")
        daily_limit = max(0.0, profile.paper_balance * profile.daily_loss_pct / 100.0)
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
            if profile.sizing_mode in {PositionSizingMode.FIXED_USDT,
                                       PositionSizingMode.EQUITY_PERCENT,
                                       PositionSizingMode.COPY_MULTIPLIER}:
                if profile.sizing_mode is PositionSizingMode.FIXED_USDT:
                    notional = float(profile.fixed_usdt)
                elif profile.sizing_mode is PositionSizingMode.EQUITY_PERCENT:
                    notional = balance * float(profile.equity_pct) / 100.0
                else:
                    source_notional = signal.get("source_notional") or signal.get("copy_notional")
                    if source_notional is None:
                        return ExecutionDecision(False, "SOURCE_SIZE_MISSING",
                                                 "Proportional sizing requires a trusted source notional")
                    notional = float(source_notional) * float(profile.copy_multiplier)
                if notional <= 0:
                    return ExecutionDecision(False, "INVALID_POSITION_SIZE", "Position notional must be positive")
                quantity = notional / price
                stop_distance = abs(price - float(signal["stop"]))
                size = PositionSize(
                    quantity=quantity, notional=notional, risk_amount=quantity * stop_distance,
                    stop_distance_pct=stop_distance / price * 100.0,
                )
            else:
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
            portfolio_limit = balance * profile.max_portfolio_exposure_pct / 100.0
            remaining_exposure = max(0.0, portfolio_limit - max(0.0, state.unified_gross_notional))
            if remaining_exposure <= 0:
                return ExecutionDecision(False, "MAX_PORTFOLIO_EXPOSURE",
                                         "Portfolio exposure limit reached")
            maximum_affordable = max(0.0, balance * max(1, profile.leverage))
            safe_notional = min(size.notional, remaining_exposure, maximum_affordable)
            if safe_notional < size.notional:
                scale = safe_notional / size.notional
                size = type(size)(
                    quantity=size.quantity * scale, notional=safe_notional,
                    risk_amount=size.risk_amount * scale,
                    stop_distance_pct=size.stop_distance_pct,
                )
            decimals = max(0, min(12, int(os.getenv("PAPER_QUANTITY_DECIMALS", "8"))))
            factor = 10 ** decimals
            rounded_quantity = int(size.quantity * factor) / factor
            rounded_notional = rounded_quantity * price
            min_notional = max(0.0, float(os.getenv("PAPER_MIN_NOTIONAL_USDT", "5")))
            if rounded_quantity <= 0 or rounded_notional < min_notional:
                return ExecutionDecision(False, "BELOW_MINIMUM_ORDER",
                                         "Safe rounded size is below the paper minimum; risk was not increased")
            stop_distance = abs(price - float(signal["stop"]))
            size = type(size)(
                quantity=rounded_quantity, notional=rounded_notional,
                risk_amount=rounded_quantity * stop_distance,
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

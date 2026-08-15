from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from database.database import connect
from services.execution_models import ExecutionMode
from services.exchanges.models import ExchangeCapabilities, ExchangeCapability


REQUIRED_LIVE_CAPABILITIES = frozenset({
    ExchangeCapability.ACCOUNT_SYNC, ExchangeCapability.BALANCES, ExchangeCapability.SYMBOL_RULES,
    ExchangeCapability.PLACE_ORDER, ExchangeCapability.CANCEL_ORDER, ExchangeCapability.QUERY_ORDER,
    ExchangeCapability.QUERY_BY_CLIENT_ID, ExchangeCapability.OPEN_ORDERS, ExchangeCapability.FILLS,
    ExchangeCapability.POSITIONS, ExchangeCapability.REDUCE_ONLY, ExchangeCapability.SERVER_TIME,
})


@dataclass(frozen=True, slots=True)
class ReadinessContext:
    environment: str
    feature_flag: bool = False
    account_enabled: bool = False
    confirmed: bool = False
    credentials_present: bool = False
    trading_permission: bool = False
    withdrawal_enabled: bool | None = None
    balances_available: bool = False
    risk_profile_complete: bool = False
    account_synced: bool = False
    server_time_synced: bool = False
    symbol_rules_valid: bool = False
    portfolio_resolved: bool = False
    recovery_required: int = 0
    reconciliation_safe: bool = False
    daily_loss_protection: bool = False
    max_order_notional: float | None = None
    max_account_exposure: float | None = None
    max_leverage: int | None = None
    kill_switch_available: bool = False
    kill_switch_active: bool = True
    capabilities: ExchangeCapabilities = ExchangeCapabilities()
    recent_certification: bool = False
    production_adapter_allowed: bool = False
    account_mode_known: bool = False


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    ready: bool
    reason_codes: tuple[str, ...]


def evaluate_live_readiness(context: ReadinessContext) -> ReadinessResult:
    failures: list[str] = []
    checks = (
        (context.feature_flag, "FEATURE_FLAG_DISABLED"),
        (context.account_enabled, "ACCOUNT_NOT_ENABLED"),
        (context.confirmed, "TWO_STEP_CONFIRMATION_REQUIRED"),
        (context.credentials_present, "CREDENTIALS_MISSING"),
        (context.trading_permission, "TRADING_PERMISSION_MISSING"),
        (context.withdrawal_enabled is False, "WITHDRAWAL_PERMISSION_UNRESOLVED_OR_ENABLED"),
        (context.balances_available, "BALANCE_READ_FAILED"),
        (context.risk_profile_complete, "RISK_PROFILE_INCOMPLETE"),
        (context.account_synced, "ACCOUNT_SYNC_FAILED"),
        (context.server_time_synced, "SERVER_TIME_UNSYNCED"),
        (context.symbol_rules_valid, "SYMBOL_RULES_INVALID"),
        (context.portfolio_resolved, "PORTFOLIO_UNRESOLVED"),
        (context.recovery_required == 0, "RECOVERY_REQUIRED"),
        (context.reconciliation_safe, "RECONCILIATION_UNSAFE"),
        (context.daily_loss_protection, "DAILY_LOSS_PROTECTION_MISSING"),
        ((context.max_order_notional or 0) > 0, "MAX_ORDER_NOTIONAL_MISSING"),
        ((context.max_account_exposure or 0) > 0, "MAX_ACCOUNT_EXPOSURE_MISSING"),
        ((context.max_leverage or 0) > 0, "MAX_LEVERAGE_MISSING"),
        (context.kill_switch_available, "KILL_SWITCH_UNAVAILABLE"),
        (not context.kill_switch_active, "KILL_SWITCH_ACTIVE"),
        (context.environment.lower() in {"production", "render"}, "DEPLOYMENT_ENVIRONMENT_INVALID"),
        (context.recent_certification, "CERTIFICATION_REQUIRED_OR_EXPIRED"),
        (context.production_adapter_allowed, "PRODUCTION_ADAPTER_NOT_ALLOWED"),
        (context.account_mode_known, "ACCOUNT_MODE_UNKNOWN"),
    )
    failures.extend(code for passed, code in checks if not passed)
    failures.extend(f"CAPABILITY_MISSING_{item.value.upper()}" for item in sorted(
        REQUIRED_LIVE_CAPABILITIES - context.capabilities.supported, key=lambda value: value.value))
    return ReadinessResult(not failures, tuple(failures))


def configured_mode() -> ExecutionMode:
    raw = os.getenv("EXECUTION_MODE", "PAPER").strip().upper()
    try:
        mode = ExecutionMode(raw)
    except ValueError:
        return ExecutionMode.DISABLED
    if mode is ExecutionMode.LIVE and os.getenv("LIVE_EXECUTION_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        return ExecutionMode.DISABLED
    return mode


def audit_readiness(*, telegram_id: int, account_id: int | None, exchange: str,
                    mode: ExecutionMode, context: ReadinessContext) -> ReadinessResult:
    result = evaluate_live_readiness(context)
    snapshot = asdict(context)
    snapshot.pop("capabilities", None)
    snapshot["capabilities"] = sorted(item.value for item in context.capabilities.supported)
    with connect() as conn:
        conn.execute("""
            INSERT INTO live_readiness_audits(telegram_id,account_id,exchange,requested_mode,ready,
                reason_codes_json,snapshot_json,created_at) VALUES(?,?,?,?,?,?,?,?)
        """, (telegram_id, account_id, exchange, mode.value, int(result.ready),
              json.dumps(result.reason_codes), json.dumps(snapshot, sort_keys=True),
              datetime.now(timezone.utc).isoformat()))
    return result

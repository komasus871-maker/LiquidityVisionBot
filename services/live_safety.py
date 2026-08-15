from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from database.database import connect
from services.exchanges.models import ExchangeOrderRequest


RISK_POLICY_VERSION = "live-risk-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): ("[REDACTED]" if any(token in str(key).lower()
                                            for token in ("secret", "api_key", "token", "passphrase"))
                           else _safe(child)) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return str(value)[:500] if not isinstance(value, (str, int, float, bool, type(None))) else value


class LiveAuditRepository:
    def record(self, *, event_type: str, outcome: str, telegram_id: int | None = None,
               account_id: int | None = None, exchange: str | None = None,
               metadata: Mapping[str, Any] | None = None) -> str:
        event_key = str(uuid.uuid4())
        payload = json.dumps(_safe(metadata or {}), sort_keys=True, separators=(",", ":"))
        with connect() as conn:
            conn.execute("""INSERT INTO live_audit_events(event_key,telegram_id,account_id,exchange,
                event_type,outcome,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (event_key, telegram_id, account_id, exchange, event_type, outcome, payload, _now()))
        return event_key


class LiveKillSwitchRepository:
    """Durable global/exchange/user/connection entry switches; close policy is separate."""

    VALID_SCOPES = frozenset({"GLOBAL", "EXCHANGE", "USER", "CONNECTION"})

    def set(self, *, scope: str, scope_key: str, active: bool, reason_code: str,
            actor_telegram_id: int | None = None) -> None:
        normalized = scope.upper()
        if normalized not in self.VALID_SCOPES:
            raise ValueError("invalid LIVE kill-switch scope")
        with connect() as conn:
            conn.execute("""INSERT INTO live_kill_switches(scope,scope_key,active,reason_code,
                actor_telegram_id,updated_at) VALUES(?,?,?,?,?,?)
                ON CONFLICT(scope,scope_key) DO UPDATE SET active=excluded.active,
                    reason_code=excluded.reason_code,actor_telegram_id=excluded.actor_telegram_id,
                    updated_at=excluded.updated_at""", (
                    normalized, str(scope_key), int(active), str(reason_code)[:80],
                    actor_telegram_id, _now()))

    def blockers(self, *, exchange: str, telegram_id: int, account_id: int) -> tuple[str, ...]:
        blockers = []
        if os.getenv("LIVE_EXECUTION_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
            blockers.append("GLOBAL_LIVE_KILL_SWITCH")
        exchange_var = f"LIVE_EXCHANGE_{exchange.upper()}_ENABLED"
        if os.getenv(exchange_var, "false").strip().lower() not in {"1", "true", "yes", "on"}:
            blockers.append("EXCHANGE_LIVE_KILL_SWITCH")
        targets = (("GLOBAL", "GLOBAL"), ("EXCHANGE", exchange.lower()),
                   ("USER", str(int(telegram_id))), ("CONNECTION", str(int(account_id))))
        with connect() as conn:
            for scope, key in targets:
                row = conn.execute("SELECT active,reason_code FROM live_kill_switches WHERE scope=? AND scope_key=?",
                                   (scope, key)).fetchone()
                if row and bool(row["active"]):
                    blockers.append(f"{scope}_KILL_SWITCH:{row['reason_code']}")
        return tuple(blockers)


class LiveRiskRepository:
    def ensure_blocked(self, *, account_id: int, telegram_id: int) -> dict[str, Any]:
        now = _now()
        with connect() as conn:
            conn.execute("""INSERT INTO live_risk_profiles(account_id,telegram_id,policy_version,status,
                created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(account_id) DO NOTHING""",
                (account_id, telegram_id, RISK_POLICY_VERSION, "BLOCKED", now, now))
            row = conn.execute("SELECT * FROM live_risk_profiles WHERE account_id=?", (account_id,)).fetchone()
        return self._decode(dict(row))

    def get(self, account_id: int) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM live_risk_profiles WHERE account_id=?", (account_id,)).fetchone()
        return self._decode(dict(row)) if row else None

    def configure(self, *, account_id: int, telegram_id: int, max_positions: int,
                  max_order_notional: Decimal, max_portfolio_exposure: Decimal,
                  max_symbol_exposure: Decimal, max_daily_realized_loss: Decimal,
                  max_daily_total_loss: Decimal, max_modeled_slippage_bps: Decimal,
                  cooldown_seconds: int, allowed_symbols: list[str], blocked_symbols: list[str],
                  allowed_timeframes: list[str], allowed_strategies: list[str],
                  allowed_directions: list[str], leverage_cap: int,
                  actor_telegram_id: int | None = None) -> dict[str, Any]:
        """Install a complete policy while enforcing non-bypassable global ceilings."""
        ceilings = {
            "max_positions": int(os.getenv("LIVE_GLOBAL_MAX_POSITIONS", "3")),
            "max_order_notional": Decimal(os.getenv("LIVE_GLOBAL_MAX_ORDER_NOTIONAL", "100")),
            "max_portfolio_exposure": Decimal(os.getenv("LIVE_GLOBAL_MAX_PORTFOLIO_EXPOSURE", "500")),
            "max_symbol_exposure": Decimal(os.getenv("LIVE_GLOBAL_MAX_SYMBOL_EXPOSURE", "200")),
            "max_daily_realized_loss": Decimal(os.getenv("LIVE_GLOBAL_MAX_DAILY_REALIZED_LOSS", "100")),
            "max_daily_total_loss": Decimal(os.getenv("LIVE_GLOBAL_MAX_DAILY_TOTAL_LOSS", "150")),
            "max_modeled_slippage_bps": Decimal(os.getenv("LIVE_GLOBAL_MAX_SLIPPAGE_BPS", "50")),
            "leverage_cap": int(os.getenv("LIVE_GLOBAL_MAX_LEVERAGE", "3")),
            "cooldown_seconds": int(os.getenv("LIVE_GLOBAL_MIN_COOLDOWN_SECONDS", "30")),
        }
        values = {
            "max_positions": int(max_positions), "max_order_notional": Decimal(max_order_notional),
            "max_portfolio_exposure": Decimal(max_portfolio_exposure),
            "max_symbol_exposure": Decimal(max_symbol_exposure),
            "max_daily_realized_loss": Decimal(max_daily_realized_loss),
            "max_daily_total_loss": Decimal(max_daily_total_loss),
            "max_modeled_slippage_bps": Decimal(max_modeled_slippage_bps),
            "cooldown_seconds": int(cooldown_seconds), "leverage_cap": int(leverage_cap),
        }
        if any(value <= 0 for value in values.values()):
            raise ValueError("LIVE_RISK_LIMITS_MUST_BE_POSITIVE")
        for key in ("max_positions", "max_order_notional", "max_portfolio_exposure",
                    "max_symbol_exposure", "max_daily_realized_loss", "max_daily_total_loss",
                    "max_modeled_slippage_bps", "leverage_cap"):
            if values[key] > ceilings[key]:
                raise ValueError(f"{key.upper()}_EXCEEDS_GLOBAL_CEILING")
        if values["cooldown_seconds"] < ceilings["cooldown_seconds"]:
            raise ValueError("COOLDOWN_SECONDS_BELOW_GLOBAL_FLOOR")
        normalized_symbols = sorted({"".join(char for char in item.upper() if char.isalnum())
                                     for item in allowed_symbols if item.strip()})
        normalized_directions = sorted({item.upper() for item in allowed_directions
                                        if item.upper() in {"BUY", "SELL"}})
        if not normalized_symbols or not allowed_timeframes or not allowed_strategies or not normalized_directions:
            raise ValueError("LIVE_RISK_ALLOWLISTS_REQUIRED")
        now = _now()
        with connect() as conn:
            account = conn.execute("SELECT telegram_id FROM live_exchange_accounts WHERE id=?",
                                   (account_id,)).fetchone()
            if not account or int(account["telegram_id"]) != int(telegram_id):
                raise PermissionError("LIVE_ACCOUNT_OWNERSHIP_MISMATCH")
            conn.execute("""INSERT INTO live_risk_profiles(account_id,telegram_id,policy_version,status,
                max_positions,max_order_notional,max_portfolio_exposure,max_symbol_exposure,
                max_daily_realized_loss,max_daily_total_loss,max_modeled_slippage_bps,cooldown_seconds,
                allowed_symbols_json,blocked_symbols_json,allowed_timeframes_json,allowed_strategies_json,
                allowed_directions_json,leverage_cap,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(account_id) DO UPDATE SET policy_version=excluded.policy_version,
                status=excluded.status,max_positions=excluded.max_positions,
                max_order_notional=excluded.max_order_notional,
                max_portfolio_exposure=excluded.max_portfolio_exposure,
                max_symbol_exposure=excluded.max_symbol_exposure,
                max_daily_realized_loss=excluded.max_daily_realized_loss,
                max_daily_total_loss=excluded.max_daily_total_loss,
                max_modeled_slippage_bps=excluded.max_modeled_slippage_bps,
                cooldown_seconds=excluded.cooldown_seconds,
                allowed_symbols_json=excluded.allowed_symbols_json,
                blocked_symbols_json=excluded.blocked_symbols_json,
                allowed_timeframes_json=excluded.allowed_timeframes_json,
                allowed_strategies_json=excluded.allowed_strategies_json,
                allowed_directions_json=excluded.allowed_directions_json,
                leverage_cap=excluded.leverage_cap,updated_at=excluded.updated_at""", (
                account_id, telegram_id, RISK_POLICY_VERSION, "ACTIVE", values["max_positions"],
                str(values["max_order_notional"]), str(values["max_portfolio_exposure"]),
                str(values["max_symbol_exposure"]), str(values["max_daily_realized_loss"]),
                str(values["max_daily_total_loss"]), str(values["max_modeled_slippage_bps"]),
                values["cooldown_seconds"], json.dumps(normalized_symbols),
                json.dumps(sorted(set(blocked_symbols))), json.dumps(sorted(set(allowed_timeframes))),
                json.dumps(sorted(set(allowed_strategies))), json.dumps(normalized_directions),
                values["leverage_cap"], now, now))
            conn.execute("""UPDATE live_exchange_accounts SET max_order_notional=?,
                max_account_exposure=?,max_leverage=?,certification_invalidated_at=?,
                certification_invalidation_reason='RISK_POLICY_CHANGED',updated_at=?
                WHERE id=? AND telegram_id=?""", (
                str(values["max_order_notional"]), str(values["max_portfolio_exposure"]),
                values["leverage_cap"], now, now, account_id, telegram_id))
        LiveAuditRepository().record(
            event_type="RISK_PROFILE_CHANGED", outcome="ACTIVE", telegram_id=telegram_id,
            account_id=account_id, metadata={"policy_version": RISK_POLICY_VERSION,
            "actor_telegram_id": actor_telegram_id, "global_ceilings_enforced": True})
        return self.get(account_id) or {}

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        for key in ("allowed_symbols_json", "blocked_symbols_json", "allowed_timeframes_json",
                    "allowed_strategies_json", "allowed_directions_json"):
            try:
                row[key.removesuffix("_json")] = json.loads(row.get(key) or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                row[key.removesuffix("_json")] = []
        return row

    def evaluate(self, *, profile: Mapping[str, Any], request: ExchangeOrderRequest,
                 current_positions: int = 0, current_portfolio_exposure: Decimal = Decimal("0"),
                 current_symbol_exposure: Decimal = Decimal("0"), modeled_slippage_bps: Decimal | None = None,
                 timeframe: str | None = None, strategy: str | None = None,
                 daily_realized_loss: Decimal | None = None,
                 daily_total_loss: Decimal | None = None,
                 seconds_since_last_entry: int | None = None) -> tuple[str, ...]:
        failures = []
        if profile.get("status") != "ACTIVE" or profile.get("policy_version") != RISK_POLICY_VERSION:
            failures.append("RISK_PROFILE_NOT_ACTIVE")
        price = request.price
        notional = request.quantity * price if price is not None else None
        checks = (
            (profile.get("max_positions"), current_positions + 1, "MAX_POSITION_COUNT"),
            (profile.get("max_order_notional"), notional, "MAX_ORDER_NOTIONAL"),
            (profile.get("max_portfolio_exposure"), current_portfolio_exposure + (notional or 0), "MAX_PORTFOLIO_EXPOSURE"),
            (profile.get("max_symbol_exposure"), current_symbol_exposure + (notional or 0), "MAX_SYMBOL_EXPOSURE"),
            (profile.get("leverage_cap"), request.leverage, "LEVERAGE_CAP"),
            (profile.get("max_modeled_slippage_bps"), modeled_slippage_bps, "MAX_MODELED_SLIPPAGE"),
            (profile.get("max_daily_realized_loss"), daily_realized_loss, "MAX_DAILY_REALIZED_LOSS"),
            (profile.get("max_daily_total_loss"), daily_total_loss, "MAX_DAILY_TOTAL_LOSS"),
        )
        for limit, value, code in checks:
            if limit is None:
                failures.append(f"{code}_MISSING")
            elif value is None:
                failures.append(f"{code}_UNRESOLVED")
            elif Decimal(str(value)) > Decimal(str(limit)):
                failures.append(f"{code}_EXCEEDED")
        cooldown = profile.get("cooldown_seconds")
        if cooldown is None:
            failures.append("COOLDOWN_MISSING")
        elif seconds_since_last_entry is None:
            failures.append("COOLDOWN_UNRESOLVED")
        elif seconds_since_last_entry < int(cooldown):
            failures.append("COOLDOWN_ACTIVE")
        symbol = "".join(char for char in request.symbol.upper() if char.isalnum())
        allowed = {"".join(char for char in str(item).upper() if char.isalnum())
                   for item in profile.get("allowed_symbols") or []}
        blocked = {"".join(char for char in str(item).upper() if char.isalnum())
                   for item in profile.get("blocked_symbols") or []}
        if not allowed or symbol not in allowed:
            failures.append("SYMBOL_NOT_ALLOWED")
        if symbol in blocked:
            failures.append("SYMBOL_BLOCKED")
        allowed_timeframes = set(profile.get("allowed_timeframes") or [])
        allowed_strategies = set(profile.get("allowed_strategies") or [])
        if not timeframe:
            failures.append("TIMEFRAME_UNRESOLVED")
        elif not allowed_timeframes or timeframe not in allowed_timeframes:
            failures.append("TIMEFRAME_NOT_ALLOWED")
        if not strategy:
            failures.append("STRATEGY_UNRESOLVED")
        elif not allowed_strategies or strategy not in allowed_strategies:
            failures.append("STRATEGY_NOT_ALLOWED")
        if request.side.upper() not in {str(item).upper() for item in profile.get("allowed_directions") or []}:
            failures.append("DIRECTION_NOT_ALLOWED")
        return tuple(dict.fromkeys(failures))


def intent_checksum(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()

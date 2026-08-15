from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import uuid
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable, Mapping

from database.database import (
    acquire_lease, connect, release_lease, runtime_finished, runtime_started,
)
from services.execution_models import ExecutionMode
from services.exchanges.base import ExchangeAdapter
from services.exchanges.models import ExchangeBalance, ExchangeCapability, ExchangeOrderRequest, SymbolRules
from services.live_accounts import LiveAccountRepository
from services.live_execution import LiveExecutionCoordinator, LiveExecutionState
from services.live_reconciliation import LiveReconciliationService
from services.live_safety import LiveAuditRepository, LiveKillSwitchRepository, LiveRiskRepository
from services.intelligence_alerts import IntelligenceAlertService
from services.localization import LocalizationService


LIVE_COPY_VERSION = "live-copy-dispatcher-v1"
LIVE_SETTINGS_VERSION = "live-copy-settings-v1"
DAILY_PNL_VERSION = "exchange-daily-pnl-v1"
RECOVERY_VERSION = "live-recovery-v1"
QUEUE_ACTIVE_STATES = ("PLANNED", "CLAIMED", "SUBMITTING", "ACKNOWLEDGED",
                       "PARTIALLY_FILLED", "UNKNOWN", "RECOVERY_REQUIRED", "RETRY_WAIT")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_symbol(value: Any) -> str:
    return "".join(char for char in str(value).upper() if char.isalnum())


def _json_list(value: Any, *, upper: bool = True) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = [item for item in value.split(",") if item]
    if not isinstance(value, (list, tuple, set)):
        return []
    normalized = []
    for item in value:
        text = str(item).strip()
        if text:
            normalized.append(text.upper() if upper else text.lower())
    return sorted(set(normalized))[:100]


class LiveCopySettingsRepository:
    """Per-connection consent and filters. Settings never override server ceilings."""

    def ensure(self, *, telegram_id: int, account_id: int, exchange: str) -> dict[str, Any]:
        now = _now().isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO live_copy_settings(telegram_id,account_id,exchange,
                enabled,minimum_quality,max_leverage,settings_version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id) DO NOTHING""",
                (telegram_id, account_id, exchange.lower(), 0, 70, 1,
                 LIVE_SETTINGS_VERSION, now, now))
            row = conn.execute("SELECT * FROM live_copy_settings WHERE account_id=? AND telegram_id=?",
                               (account_id, telegram_id)).fetchone()
        if not row:
            raise PermissionError("LIVE_COPY_SETTINGS_OWNERSHIP_MISMATCH")
        return self._decode(dict(row))

    def configure(self, *, telegram_id: int, account_id: int, enabled: bool,
                  symbols: list[str], strategies: list[str], timeframes: list[str],
                  directions: list[str], minimum_quality: Decimal,
                  sizing_mode: str, sizing_value: Decimal,
                  max_exposure: Decimal, max_leverage: int) -> dict[str, Any]:
        account = LiveAccountRepository().get_by_id(account_id)
        if account is None or account.telegram_id != int(telegram_id):
            raise PermissionError("LIVE_COPY_ACCOUNT_OWNERSHIP_MISMATCH")
        mode = str(sizing_mode).upper()
        if mode not in {"FIXED_NOTIONAL", "EQUITY_PERCENT", "RISK_PERCENT"}:
            raise ValueError("LIVE_COPY_SIZING_MODE_INVALID")
        if not Decimal("0") < Decimal(str(sizing_value)) <= Decimal("1000000"):
            raise ValueError("LIVE_COPY_SIZING_VALUE_INVALID")
        if not Decimal("0") < Decimal(str(max_exposure)) <= Decimal(str(
                os.getenv("LIVE_SERVER_MAX_ACCOUNT_EXPOSURE", "10000"))):
            raise ValueError("LIVE_COPY_MAX_EXPOSURE_INVALID")
        if not Decimal("0") <= Decimal(str(minimum_quality)) <= Decimal("100"):
            raise ValueError("LIVE_COPY_MINIMUM_QUALITY_INVALID")
        server_leverage = max(1, int(os.getenv("LIVE_SERVER_MAX_LEVERAGE", "3")))
        if not 1 <= int(max_leverage) <= server_leverage:
            raise ValueError("LIVE_COPY_LEVERAGE_EXCEEDS_SERVER_LIMIT")
        normalized_symbols = [_canonical_symbol(item) for item in _json_list(symbols)]
        if not normalized_symbols:
            raise ValueError("LIVE_COPY_SYMBOLS_REQUIRED")
        normalized_directions = _json_list(directions)
        if any(item not in {"BUY", "SELL", "LONG", "SHORT"} for item in normalized_directions):
            raise ValueError("LIVE_COPY_DIRECTION_INVALID")
        now = _now().isoformat()
        with connect() as conn:
            cur = conn.execute("""UPDATE live_copy_settings SET enabled=?,symbols_json=?,
                strategies_json=?,timeframes_json=?,directions_json=?,minimum_quality=?,
                sizing_mode=?,sizing_value=?,max_exposure=?,max_leverage=?,settings_version=?,updated_at=?
                WHERE account_id=? AND telegram_id=?""", (
                int(enabled), json.dumps(normalized_symbols), json.dumps(_json_list(strategies)),
                json.dumps(_json_list(timeframes, upper=False)), json.dumps(normalized_directions),
                float(minimum_quality), mode, float(sizing_value), float(max_exposure),
                int(max_leverage), LIVE_SETTINGS_VERSION, now, account_id, telegram_id))
        if cur.rowcount != 1:
            self.ensure(telegram_id=telegram_id, account_id=account_id, exchange=account.exchange)
            return self.configure(telegram_id=telegram_id, account_id=account_id, enabled=enabled,
                                  symbols=symbols, strategies=strategies, timeframes=timeframes,
                                  directions=directions, minimum_quality=minimum_quality,
                                  sizing_mode=mode, sizing_value=sizing_value,
                                  max_exposure=max_exposure, max_leverage=max_leverage)
        LiveAuditRepository().record(
            event_type="LIVE_COPY_SETTINGS", outcome="ENABLED" if enabled else "DISABLED",
            telegram_id=telegram_id, account_id=account_id, exchange=account.exchange,
            metadata={"settings_version": LIVE_SETTINGS_VERSION, "symbols": normalized_symbols,
                      "sizing_mode": mode, "secrets_present": False})
        return self.get(account_id, telegram_id=telegram_id) or {}

    def get(self, account_id: int, *, telegram_id: int | None = None) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("""SELECT * FROM live_copy_settings WHERE account_id=?
                AND (? IS NULL OR telegram_id=?)""", (account_id, telegram_id, telegram_id)).fetchone()
        return self._decode(dict(row)) if row else None

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        for key in ("symbols", "strategies", "timeframes", "directions"):
            row[key] = _json_list(row.pop(f"{key}_json", "[]"), upper=key != "timeframes")
        row["enabled"] = bool(row.get("enabled"))
        return row

    @staticmethod
    def eligible(settings: Mapping[str, Any], plan: Mapping[str, Any], quality: float | None) -> tuple[bool, str]:
        if not settings.get("enabled"):
            return False, "LIVE_COPY_DISABLED"
        checks = (
            (not settings.get("symbols") or _canonical_symbol(plan.get("symbol")) in settings["symbols"],
             "LIVE_COPY_SYMBOL_FILTER"),
            (not settings.get("strategies") or str(plan.get("strategy") or
                 (plan.get("profile_snapshot") or {}).get("strategy") or "").upper() in settings["strategies"],
             "LIVE_COPY_STRATEGY_FILTER"),
            (not settings.get("timeframes") or str(plan.get("timeframe") or "").lower() in settings["timeframes"],
             "LIVE_COPY_TIMEFRAME_FILTER"),
            (not settings.get("directions") or str(plan.get("side") or "").upper() in settings["directions"],
             "LIVE_COPY_DIRECTION_FILTER"),
            (quality is not None and quality >= float(settings.get("minimum_quality") or 0),
             "LIVE_COPY_MINIMUM_QUALITY"),
        )
        return next(((False, code) for passed, code in checks if not passed), (True, "ELIGIBLE"))


class LiveDailyPnlService:
    """UTC-bucketed exchange truth. Missing authoritative data is never treated as zero."""

    @staticmethod
    def bucket(moment: datetime | None = None) -> str:
        current = moment or _now()
        current = current if current.tzinfo else current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).date().isoformat()

    async def refresh(self, *, adapter: ExchangeAdapter, telegram_id: int,
                      account_id: int, exchange: str, symbols: list[str]) -> dict[str, Any]:
        if not adapter.capabilities().supports(ExchangeCapability.FILLS):
            return self._persist_failure(telegram_id, account_id, exchange, symbols,
                                         "DAILY_PNL_FILLS_UNSUPPORTED")
        normalized = sorted({_canonical_symbol(symbol) for symbol in symbols if _canonical_symbol(symbol)})
        if not normalized:
            return self._persist_failure(telegram_id, account_id, exchange, normalized,
                                         "DAILY_PNL_SYMBOL_UNIVERSE_EMPTY")
        start = datetime.combine(_now().date(), datetime.min.time(), tzinfo=timezone.utc)
        all_fills = {}
        try:
            for symbol in normalized:
                fills = await adapter.fills(symbol=symbol, order_id=None)
                for fill in fills:
                    if fill.filled_at_ms is None:
                        raise ValueError("DAILY_PNL_FILL_TIMESTAMP_MISSING")
                    filled = datetime.fromtimestamp(fill.filled_at_ms / 1000, tz=timezone.utc)
                    if filled >= start:
                        all_fills[(fill.symbol, fill.fill_id)] = fill
            positions = await adapter.positions()
        except Exception as exc:
            code = str(exc) if str(exc).startswith("DAILY_PNL_") else "DAILY_PNL_SOURCE_UNAVAILABLE"
            return self._persist_failure(telegram_id, account_id, exchange, normalized, code)
        realized = sum((fill.realized_pnl for fill in all_fills.values()), Decimal("0"))
        fees = sum((abs(fill.commission) for fill in all_fills.values()), Decimal("0"))
        unrealized = sum((position.unrealized_pnl for position in positions), Decimal("0"))
        total_basis = realized - fees + min(unrealized, Decimal("0"))
        now = _now().isoformat()
        bucket = self.bucket()
        with connect() as conn:
            conn.execute("""INSERT INTO live_daily_pnl_snapshots(account_id,telegram_id,exchange,
                bucket_utc,realized_pnl,fees,unrealized_pnl,total_loss_basis,source_identity,
                source_symbols_json,source_complete,state,rejection_code,observed_at,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,bucket_utc) DO UPDATE SET
                realized_pnl=excluded.realized_pnl,fees=excluded.fees,
                unrealized_pnl=excluded.unrealized_pnl,total_loss_basis=excluded.total_loss_basis,
                source_identity=excluded.source_identity,source_symbols_json=excluded.source_symbols_json,
                source_complete=excluded.source_complete,state=excluded.state,rejection_code=NULL,
                observed_at=excluded.observed_at""", (
                account_id, telegram_id, exchange, bucket, float(realized), float(fees), float(unrealized),
                float(total_basis), f"{exchange.upper()}_FILLS_POSITIONS_REALIZED_FEES:{DAILY_PNL_VERSION}",
                json.dumps(normalized), 1, "HEALTHY", None, now, now))
        return self.latest(account_id) or {}

    def _persist_failure(self, telegram_id: int, account_id: int, exchange: str,
                         symbols: list[str], code: str) -> dict[str, Any]:
        now, bucket = _now().isoformat(), self.bucket()
        with connect() as conn:
            conn.execute("""INSERT INTO live_daily_pnl_snapshots(account_id,telegram_id,exchange,
                bucket_utc,realized_pnl,fees,unrealized_pnl,total_loss_basis,source_identity,
                source_symbols_json,source_complete,state,rejection_code,observed_at,created_at)
                VALUES(?,?,?,?,0,0,NULL,0,?,?,?,?,?,?,?) ON CONFLICT(account_id,bucket_utc) DO UPDATE SET
                source_identity=excluded.source_identity,source_symbols_json=excluded.source_symbols_json,
                source_complete=0,state='FAILED',rejection_code=excluded.rejection_code,
                observed_at=excluded.observed_at""", (
                account_id, telegram_id, exchange, bucket, f"{exchange.upper()}_AUTHORITATIVE_UNAVAILABLE",
                json.dumps(symbols), 0, "FAILED", code[:80], now, now))
        return self.latest(account_id) or {}

    def latest(self, account_id: int) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("""SELECT * FROM live_daily_pnl_snapshots
                WHERE account_id=? ORDER BY observed_at DESC,id DESC LIMIT 1""", (account_id,)).fetchone()
        return dict(row) if row else None

    def require_current(self, account_id: int, *, maximum_age_seconds: int = 300) -> dict[str, Any]:
        row = self.latest(account_id)
        if not row or row.get("bucket_utc") != self.bucket() or row.get("state") != "HEALTHY" or not row.get("source_complete"):
            raise PermissionError("LIVE_DAILY_PNL_UNAVAILABLE")
        observed = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
        observed = observed if observed.tzinfo else observed.replace(tzinfo=timezone.utc)
        if (_now() - observed).total_seconds() > maximum_age_seconds:
            raise PermissionError("LIVE_DAILY_PNL_STALE")
        return row


class LiveExecutionQueueRepository:
    def enqueue(self, *, journal_row: Mapping[str, Any], account_id: int,
                exchange: str, quality: float | None, settings: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        try:
            plan = json.loads(str(journal_row.get("plan_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise PermissionError("LIVE_QUEUE_PLAN_INVALID") from None
        if plan.get("status") != "APPROVED" or not journal_row.get("plan_id"):
            raise PermissionError("LIVE_QUEUE_APPROVED_PLAN_REQUIRED")
        if int(journal_row.get("exchange_account_id") or 0) != int(account_id):
            raise PermissionError("LIVE_QUEUE_ACCOUNT_IDENTITY_MISMATCH")
        eligible, code = LiveCopySettingsRepository.eligible(settings, plan, quality)
        if not eligible:
            raise PermissionError(code)
        queue_key = hashlib.sha256(
            f"{account_id}|{journal_row['plan_id']}|{LIVE_COPY_VERSION}".encode()).hexdigest()
        payload = {"version": LIVE_COPY_VERSION, "plan": plan, "quality": quality,
                   "journal_status": journal_row.get("status"), "ai_authority": False,
                   "research_authority": False}
        now = _now().isoformat()
        with connect() as conn:
            cur = conn.execute("""INSERT INTO live_execution_queue(queue_key,journal_id,plan_id,
                telegram_id,account_id,exchange,signal_id,state,payload_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'PLANNED',?,?,?) ON CONFLICT(queue_key) DO NOTHING""", (
                queue_key, int(journal_row["id"]), str(journal_row["plan_id"]),
                int(journal_row["telegram_id"]), account_id, exchange,
                int(journal_row["signal_id"]), json.dumps(payload, sort_keys=True), now, now))
            row = conn.execute("SELECT * FROM live_execution_queue WHERE queue_key=?", (queue_key,)).fetchone()
        return dict(row), cur.rowcount == 1

    def claim(self, queue_id: int, *, worker_id: str, lease_seconds: int = 120) -> tuple[dict[str, Any], bool]:
        now_dt, token = _now(), uuid.uuid4().hex
        now, expires = now_dt.isoformat(), (now_dt + timedelta(seconds=max(30, lease_seconds))).isoformat()
        with connect() as conn:
            cur = conn.execute("""UPDATE live_execution_queue SET state='CLAIMED',claim_token=?,
                claimed_by=?,claimed_at=?,lease_expires_at=?,attempt_count=attempt_count+1,updated_at=?
                WHERE id=? AND (state='PLANNED' OR (state='RETRY_WAIT' AND
                (next_attempt_at IS NULL OR next_attempt_at<=?)))""",
                (token, worker_id, now, expires, now, queue_id, now))
            row = conn.execute("SELECT * FROM live_execution_queue WHERE id=?", (queue_id,)).fetchone()
        if not row:
            raise KeyError(queue_id)
        return dict(row), cur.rowcount == 1

    def transition(self, queue_id: int, expected: str, target: str, *, execution_id: int | None = None,
                   error_code: str | None = None) -> bool:
        allowed = {
            "CLAIMED": {"SUBMITTING", "REJECTED", "RECOVERY_REQUIRED"},
            "SUBMITTING": {"ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "REJECTED", "UNKNOWN",
                           "RECOVERY_REQUIRED"},
            "ACKNOWLEDGED": {"PARTIALLY_FILLED", "FILLED", "CANCEL_PENDING", "RECOVERY_REQUIRED"},
            "PARTIALLY_FILLED": {"FILLED", "CANCEL_PENDING", "RECOVERY_REQUIRED"},
            "CANCEL_PENDING": {"CANCELED", "UNKNOWN"},
            "UNKNOWN": {"RECOVERY_REQUIRED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED"},
            "RECOVERY_REQUIRED": {"RETRY_WAIT", "ACKNOWLEDGED", "PARTIALLY_FILLED",
                                  "FILLED", "REJECTED"},
        }
        if target not in allowed.get(expected, set()):
            raise ValueError("LIVE_QUEUE_TRANSITION_INVALID")
        with connect() as conn:
            cur = conn.execute("""UPDATE live_execution_queue SET state=?,execution_id=COALESCE(?,execution_id),
                last_error_code=?,updated_at=? WHERE id=? AND state=?""",
                (target, execution_id, error_code, _now().isoformat(), queue_id, expected))
        return cur.rowcount == 1

    def schedule_retry(self, queue_id: int, *, delay_seconds: int = 30,
                       error_code: str = "RECOVERY_RETRY") -> bool:
        with connect() as conn:
            row = conn.execute("SELECT attempt_count,max_attempts FROM live_execution_queue WHERE id=?",
                               (queue_id,)).fetchone()
        if not row:
            raise KeyError(queue_id)
        target = "RETRY_WAIT" if int(row["attempt_count"]) < int(row["max_attempts"]) else "REJECTED"
        next_at = (_now() + timedelta(seconds=max(1, delay_seconds))).isoformat() if target == "RETRY_WAIT" else None
        with connect() as conn:
            cur = conn.execute("""UPDATE live_execution_queue SET state=?,next_attempt_at=?,
                last_error_code=?,claim_token=NULL,claimed_by=NULL,lease_expires_at=NULL,updated_at=?
                WHERE id=? AND state='RECOVERY_REQUIRED'""",
                (target, next_at, error_code[:80], _now().isoformat(), queue_id))
        return cur.rowcount == 1

    def seal_request(self, queue_id: int, *, request: ExchangeOrderRequest,
                     settings_version: str) -> dict[str, Any]:
        with connect() as conn:
            row = conn.execute("SELECT state,payload_json FROM live_execution_queue WHERE id=?", (queue_id,)).fetchone()
            if not row or row["state"] != "CLAIMED":
                raise PermissionError("LIVE_QUEUE_CLAIM_REQUIRED")
            payload = json.loads(row["payload_json"])
            request_payload = {
                "symbol": request.symbol, "side": request.side, "order_type": request.order_type,
                "quantity": str(request.quantity), "price": str(request.price) if request.price is not None else None,
                "leverage": request.leverage, "reduce_only": request.reduce_only,
                "client_order_id": request.client_order_id,
            }
            checksum = hashlib.sha256(json.dumps(request_payload, sort_keys=True).encode()).hexdigest()
            existing = payload.get("live_request")
            if existing and existing != request_payload:
                raise PermissionError("LIVE_QUEUE_SEALED_REQUEST_CONFLICT")
            payload.update({"live_request": request_payload, "live_request_checksum": checksum,
                            "settings_version": settings_version, "sealed_at": _now().isoformat()})
            conn.execute("UPDATE live_execution_queue SET payload_json=?,updated_at=? WHERE id=? AND state='CLAIMED'",
                         (json.dumps(payload, sort_keys=True), _now().isoformat(), queue_id))
        return payload

    def due(self, limit: int = 20) -> list[dict[str, Any]]:
        safe = max(1, min(int(limit), 100))
        with connect() as conn:
            rows = conn.execute(f"""SELECT * FROM live_execution_queue WHERE state='PLANNED'
                OR (state='RETRY_WAIT' AND (next_attempt_at IS NULL OR next_attempt_at<=?))
                ORDER BY id LIMIT {safe}""", (_now().isoformat(),)).fetchall()
        return [dict(row) for row in rows]

    def recover_expired(self) -> int:
        now = _now().isoformat()
        with connect() as conn:
            cur = conn.execute("""UPDATE live_execution_queue SET state='RECOVERY_REQUIRED',
                last_error_code='QUEUE_LEASE_EXPIRED',updated_at=?
                WHERE state IN ('CLAIMED','SUBMITTING') AND lease_expires_at<?""", (now, now))
        return cur.rowcount

    def recent(self, telegram_id: int, limit: int = 20) -> list[dict[str, Any]]:
        safe = max(1, min(int(limit), 100))
        with connect() as conn:
            rows = conn.execute(f"""SELECT * FROM live_execution_queue WHERE telegram_id=?
                ORDER BY id DESC LIMIT {safe}""", (telegram_id,)).fetchall()
        return [dict(row) for row in rows]

    def recovery_required(self, limit: int = 20) -> list[dict[str, Any]]:
        safe = max(1, min(int(limit), 100))
        with connect() as conn:
            rows = conn.execute(f"""SELECT * FROM live_execution_queue
                WHERE state='RECOVERY_REQUIRED' ORDER BY updated_at,id LIMIT {safe}""").fetchall()
        return [dict(row) for row in rows]


class LiveSizer:
    @staticmethod
    def calculate(*, settings: Mapping[str, Any], risk: Mapping[str, Any], plan: Mapping[str, Any],
                  balances: list[ExchangeBalance], rules: SymbolRules) -> dict[str, Any]:
        price = Decimal(str(plan.get("entry_price") or 0))
        if price <= 0:
            raise PermissionError("LIVE_SIZING_REFERENCE_PRICE_REQUIRED")
        equity = sum((balance.wallet_balance for balance in balances), Decimal("0"))
        available = sum((balance.available_balance for balance in balances), Decimal("0"))
        if equity <= 0 or available <= 0:
            raise PermissionError("LIVE_SIZING_EQUITY_UNAVAILABLE")
        mode, value = str(settings.get("sizing_mode")), Decimal(str(settings.get("sizing_value") or 0))
        if mode == "FIXED_NOTIONAL":
            notional = value
        elif mode == "EQUITY_PERCENT":
            if not Decimal("0") < value <= Decimal("100"):
                raise PermissionError("LIVE_SIZING_EQUITY_PERCENT_INVALID")
            notional = equity * value / Decimal("100")
        elif mode == "RISK_PERCENT":
            stop = Decimal(str(plan.get("stop_loss") or 0))
            if stop <= 0 or stop == price or not Decimal("0") < value <= Decimal("5"):
                raise PermissionError("LIVE_SIZING_RISK_INPUTS_INVALID")
            risk_amount = equity * value / Decimal("100")
            quantity = risk_amount / abs(price - stop)
            notional = quantity * price
        else:
            raise PermissionError("LIVE_SIZING_MODE_INVALID")
        ceilings = [Decimal(str(settings.get("max_exposure") or 0)),
                    Decimal(str(risk.get("max_order_notional") or 0)),
                    Decimal(str(os.getenv("LIVE_SERVER_MAX_ORDER_NOTIONAL", "100")))]
        if any(value <= 0 for value in ceilings):
            raise PermissionError("LIVE_SIZING_CEILING_UNRESOLVED")
        notional = min(notional, *ceilings, available)
        quantity = (notional / price / rules.quantity_step).to_integral_value(rounding=ROUND_DOWN) * rules.quantity_step
        if quantity < rules.min_quantity:
            raise PermissionError("LIVE_SIZING_BELOW_MIN_QUANTITY")
        if rules.min_notional is not None and quantity * price < rules.min_notional:
            raise PermissionError("LIVE_SIZING_BELOW_MIN_NOTIONAL")
        leverage_candidates = [int(settings.get("max_leverage") or 1), int(risk.get("leverage_cap") or 1),
                               int(rules.max_leverage or 1), int(os.getenv("LIVE_SERVER_MAX_LEVERAGE", "3"))]
        leverage = min(leverage_candidates)
        if leverage < 1:
            raise PermissionError("LIVE_LEVERAGE_UNRESOLVED")
        return {"quantity": quantity, "notional": quantity * price, "reference_price": price,
                "effective_leverage": leverage, "leverage_sources": leverage_candidates,
                "equity": equity, "available": available, "sizing_mode": mode}


class LiveRecoveryService:
    async def recover(self, *, adapter: ExchangeAdapter, telegram_id: int,
                      account_id: int, exchange: str) -> dict[str, Any]:
        now = _now().isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO live_recovery_state(account_id,telegram_id,exchange,state,
                last_started_at,updated_at) VALUES(?,?,?,'RUNNING',?,?)
                ON CONFLICT(account_id) DO UPDATE SET state='RUNNING',last_started_at=excluded.last_started_at,
                blocker_code=NULL,updated_at=excluded.updated_at""",
                (account_id, telegram_id, exchange, now, now))
        try:
            report = await LiveReconciliationService().reconcile(
                adapter=adapter, telegram_id=telegram_id, account_id=account_id, exchange=exchange)
            if report["status"] != "MATCHED":
                raise PermissionError("RECOVERY_RECONCILIATION_MISMATCH")
            with connect() as conn:
                unresolved = conn.execute("""SELECT COUNT(*) n FROM live_executions
                    WHERE account_id=? AND state IN ('UNKNOWN','RECOVERY_REQUIRED')""", (account_id,)).fetchone()
            if int(unresolved["n"] or 0):
                raise PermissionError("RECOVERY_UNKNOWN_EXECUTIONS")
        except Exception as exc:
            code = str(exc)[:80] or "RECOVERY_FAILED"
            with connect() as conn:
                conn.execute("""UPDATE live_recovery_state SET state='FAILED',blocker_code=?,
                    last_failure_at=?,updated_at=? WHERE account_id=?""", (code, now, now, account_id))
            return {"state": "FAILED", "blocker_code": code}
        with connect() as conn:
            conn.execute("""UPDATE live_recovery_state SET state='READY',blocker_code=NULL,
                last_success_at=?,details_json=?,updated_at=? WHERE account_id=?""",
                (now, json.dumps({"version": RECOVERY_VERSION, "reconciled": True}), now, account_id))
        return {"state": "READY", "blocker_code": None}

    @staticmethod
    def require_ready(account_id: int) -> None:
        with connect() as conn:
            row = conn.execute("SELECT state FROM live_recovery_state WHERE account_id=?", (account_id,)).fetchone()
        if not row or row["state"] != "READY":
            raise PermissionError("LIVE_RESTART_RECOVERY_REQUIRED")


class LiveCopyDispatcher:
    """Deterministic journal -> immutable intent -> LIVE adapter boundary."""

    def __init__(self, queue: LiveExecutionQueueRepository | None = None) -> None:
        self.queue = queue or LiveExecutionQueueRepository()

    def enqueue_journal(self, journal_row: Mapping[str, Any], *, quality: float | None) -> tuple[dict[str, Any], bool]:
        account_id = int(journal_row.get("exchange_account_id") or 0)
        account = LiveAccountRepository().get_by_id(account_id)
        if account is None or account.telegram_id != int(journal_row.get("telegram_id") or 0):
            raise PermissionError("LIVE_DISPATCH_ACCOUNT_OWNERSHIP_MISMATCH")
        if not account.live_enabled or account.lifecycle_state != "LIVE_ENABLED":
            raise PermissionError("LIVE_DISPATCH_ACCOUNT_NOT_ENABLED")
        settings = LiveCopySettingsRepository().get(account_id, telegram_id=account.telegram_id)
        if not settings:
            raise PermissionError("LIVE_COPY_SETTINGS_REQUIRED")
        return self.queue.enqueue(journal_row=journal_row, account_id=account_id,
                                  exchange=account.exchange, quality=quality, settings=settings)

    @staticmethod
    def _require_preflight(account_id: int) -> None:
        with connect() as conn:
            row = conn.execute("""SELECT ready,created_at FROM live_readiness_audits
                WHERE account_id=? ORDER BY id DESC LIMIT 1""", (account_id,)).fetchone()
        if not row or not bool(row["ready"]):
            raise PermissionError("LIVE_PREFLIGHT_REQUIRED")
        created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
        created = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        if (_now() - created).total_seconds() > int(os.getenv("LIVE_PREFLIGHT_MAX_AGE_SECONDS", "900")):
            raise PermissionError("LIVE_PREFLIGHT_STALE")

    async def process_claimed(self, row: Mapping[str, Any], *, adapter: ExchangeAdapter,
                              coordinator: LiveExecutionCoordinator | None = None) -> dict[str, Any]:
        if row.get("state") != "CLAIMED":
            raise PermissionError("LIVE_QUEUE_CLAIM_REQUIRED")
        if os.getenv("LIVE_DISPATCHER_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
            raise PermissionError("LIVE_DISPATCHER_DISABLED")
        account = LiveAccountRepository().get_by_id(int(row["account_id"]))
        if account is None or account.telegram_id != int(row["telegram_id"]):
            raise PermissionError("LIVE_QUEUE_OWNERSHIP_MISMATCH")
        if not account.live_enabled or account.lifecycle_state != "LIVE_ENABLED" or account.kill_switch:
            raise PermissionError("LIVE_QUEUE_ACCOUNT_NOT_ENABLED")
        settings = LiveCopySettingsRepository().get(account.id, telegram_id=account.telegram_id) or {}
        payload = json.loads(str(row["payload_json"]))
        plan = payload["plan"]
        eligible, code = LiveCopySettingsRepository.eligible(settings, plan, payload.get("quality"))
        if not eligible:
            self.queue.transition(int(row["id"]), "CLAIMED", "REJECTED", error_code=code)
            return {"state": "REJECTED", "code": code}
        LiveRecoveryService.require_ready(account.id)
        self._require_preflight(account.id)
        blockers = LiveKillSwitchRepository().blockers(
            exchange=account.exchange, telegram_id=account.telegram_id, account_id=account.id)
        if blockers:
            raise PermissionError(blockers[0].split(":", 1)[0])
        reconciliation = await LiveReconciliationService().reconcile(
            adapter=adapter, telegram_id=account.telegram_id,
            account_id=account.id, exchange=account.exchange)
        if reconciliation["status"] != "MATCHED":
            raise PermissionError("LIVE_DISPATCH_RECONCILIATION_MISMATCH")
        pnl = await LiveDailyPnlService().refresh(
            adapter=adapter, telegram_id=account.telegram_id, account_id=account.id,
            exchange=account.exchange, symbols=settings.get("symbols") or [])
        if pnl.get("state") != "HEALTHY":
            raise PermissionError("LIVE_DAILY_PNL_UNAVAILABLE")
        risk = LiveRiskRepository().get(account.id) or {}
        if risk.get("status") != "ACTIVE":
            raise PermissionError("LIVE_RISK_PROFILE_REQUIRED")
        balances = await adapter.balances()
        rules = await adapter.symbol_rules(str(plan["symbol"]))
        sizing = LiveSizer.calculate(settings=settings, risk=risk, plan=plan,
                                     balances=balances, rules=rules)
        request = ExchangeOrderRequest(
            symbol=str(plan["symbol"]), side=str(plan["side"]).upper(),
            order_type="MARKET", quantity=sizing["quantity"],
            client_order_id=f"lv{row['queue_key'][:30]}", price=sizing["reference_price"],
            leverage=int(sizing["effective_leverage"]), reduce_only=False,
            stop_loss=Decimal(str(plan["stop_loss"])) if plan.get("stop_loss") else None,
            take_profit=Decimal(str((plan.get("take_profits") or [None])[0]))
                        if (plan.get("take_profits") or [None])[0] else None,
        )
        self.queue.seal_request(int(row["id"]), request=request,
                                settings_version=str(settings.get("settings_version") or "UNKNOWN"))
        self.queue.transition(int(row["id"]), "CLAIMED", "SUBMITTING")
        coordinator = coordinator or LiveExecutionCoordinator(adapter)
        result = await coordinator.submit(
            execution_key=f"liveq-{row['queue_key']}", plan_id=str(row["plan_id"]),
            telegram_id=account.telegram_id, account_id=account.id, exchange=account.exchange,
            mode=ExecutionMode.LIVE, request=request, readiness_passed=True,
            signal_id=int(row["signal_id"]), strategy=str(plan.get("strategy") or "DETERMINISTIC"),
            timeframe=str(plan.get("timeframe") or ""),
            modeled_slippage_bps=Decimal(str(plan.get("expected_slippage_pct") or 0)) * 100,
            daily_realized_loss=abs(min(Decimal(str(pnl["realized_pnl"])), Decimal("0"))),
            daily_total_loss=abs(min(Decimal(str(pnl["total_loss_basis"])), Decimal("0"))),
            authority_source="DETERMINISTIC_APPROVED_PLAN")
        target = {LiveExecutionState.ACKNOWLEDGED: "ACKNOWLEDGED",
                  LiveExecutionState.PARTIALLY_FILLED: "PARTIALLY_FILLED",
                  LiveExecutionState.FILLED: "FILLED",
                  LiveExecutionState.UNKNOWN: "UNKNOWN"}.get(result.state, "REJECTED")
        self.queue.transition(int(row["id"]), "SUBMITTING", target,
                              execution_id=result.execution_id,
                              error_code=None if target != "REJECTED" else result.state.value)
        return {"state": target, "execution_id": result.execution_id,
                "client_order_id": result.client_order_id, "sizing": sizing,
                "daily_loss_basis": pnl.get("total_loss_basis"),
                "daily_loss_limit": risk.get("max_daily_total_loss")}


class LiveEmergencyCloseService:
    """User-owned two-step reduce-only workflow, separate from every kill switch."""

    async def begin(self, *, adapter: ExchangeAdapter, telegram_id: int,
                    account_id: int, exchange: str, ttl_seconds: int = 300) -> dict[str, Any]:
        account = LiveAccountRepository().get_by_id(account_id)
        if account is None or account.telegram_id != telegram_id or account.exchange != exchange:
            raise PermissionError("LIVE_EMERGENCY_OWNERSHIP_MISMATCH")
        positions = [position for position in await adapter.positions() if position.quantity > 0]
        if not positions:
            raise PermissionError("LIVE_EMERGENCY_NO_OPEN_POSITIONS")
        if any(position.mark_price <= 0 for position in positions):
            raise PermissionError("LIVE_EMERGENCY_EXPOSURE_AMBIGUOUS")
        snapshot = [{"symbol": position.symbol, "side": position.side,
                     "quantity": str(position.quantity), "mark_price": str(position.mark_price)}
                    for position in positions]
        exposure = sum((position.quantity * position.mark_price for position in positions), Decimal("0"))
        token = secrets.token_urlsafe(9)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        confirmation_key = uuid.uuid4().hex
        fingerprint = hashlib.sha256(
            f"{account.id}|{account.exchange}|{account.credential_ref}".encode()).hexdigest()[:12]
        now_dt = _now()
        with connect() as conn:
            conn.execute("""INSERT INTO live_emergency_confirmations(confirmation_key,telegram_id,
                account_id,exchange,token_hash,account_fingerprint,position_snapshot_json,
                estimated_exposure,state,expires_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?, 'PENDING',?,?,?)""", (
                confirmation_key, telegram_id, account_id, exchange, token_hash, fingerprint,
                json.dumps(snapshot, sort_keys=True), float(exposure),
                (now_dt + timedelta(seconds=max(30, min(ttl_seconds, 600)))).isoformat(),
                now_dt.isoformat(), now_dt.isoformat()))
        LiveAuditRepository().record(
            event_type="LIVE_EMERGENCY_CLOSE", outcome="INITIATED", telegram_id=telegram_id,
            account_id=account_id, exchange=exchange,
            metadata={"confirmation_key": confirmation_key, "position_count": len(snapshot),
                      "estimated_exposure": str(exposure), "reduce_only": True})
        return {"token": token, "confirmation_key": confirmation_key,
                "account_fingerprint": fingerprint, "positions": snapshot,
                "estimated_exposure": exposure,
                "expires_at": (now_dt + timedelta(seconds=max(30, min(ttl_seconds, 600)))).isoformat()}

    async def confirm(self, *, adapter: ExchangeAdapter, telegram_id: int, token: str,
                      coordinator: LiveExecutionCoordinator | None = None) -> dict[str, Any]:
        digest, now = hashlib.sha256(token.strip().encode()).hexdigest(), _now()
        with connect() as conn:
            rows = conn.execute("""SELECT * FROM live_emergency_confirmations
                WHERE telegram_id=? AND state='PENDING' ORDER BY id DESC LIMIT 20""", (telegram_id,)).fetchall()
        row = next((dict(item) for item in rows if hmac.compare_digest(str(item["token_hash"]), digest)), None)
        if not row:
            raise PermissionError("LIVE_EMERGENCY_TOKEN_INVALID")
        expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
        expires = expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)
        if expires < now:
            with connect() as conn:
                conn.execute("UPDATE live_emergency_confirmations SET state='EXPIRED',updated_at=? WHERE id=?",
                             (now.isoformat(), row["id"]))
            raise PermissionError("LIVE_EMERGENCY_TOKEN_EXPIRED")
        with connect() as conn:
            cur = conn.execute("""UPDATE live_emergency_confirmations SET state='CONFIRMED',
                confirmed_at=?,updated_at=? WHERE id=? AND telegram_id=? AND state='PENDING'""",
                (now.isoformat(), now.isoformat(), row["id"], telegram_id))
        if cur.rowcount != 1:
            raise PermissionError("LIVE_EMERGENCY_CONFIRMATION_CONFLICT")
        positions = [position for position in await adapter.positions() if position.quantity > 0]
        intended = {( _canonical_symbol(item["symbol"]), str(item["side"]).upper())
                    for item in json.loads(row["position_snapshot_json"])}
        current = {(_canonical_symbol(position.symbol), position.side.upper()) for position in positions}
        if not positions or not current.issubset(intended):
            code = "LIVE_EMERGENCY_POSITION_STATE_CHANGED" if positions else "LIVE_EMERGENCY_ALREADY_FLAT"
            with connect() as conn:
                conn.execute("""UPDATE live_emergency_confirmations SET state='FAILED',result_json=?,
                    completed_at=?,updated_at=? WHERE id=?""",
                    (json.dumps({"code": code}), now.isoformat(), now.isoformat(), row["id"]))
            raise PermissionError(code)
        coordinator = coordinator or LiveExecutionCoordinator(adapter)
        submissions = []
        for index, position in enumerate(positions):
            request = ExchangeOrderRequest(
                symbol=position.symbol, side="SELL" if position.side.upper() == "LONG" else "BUY",
                order_type="MARKET", quantity=position.quantity,
                client_order_id=f"emg{row['confirmation_key'][:24]}{index}",
                price=position.mark_price, leverage=max(1, int(position.leverage)),
                reduce_only=True, position_side=position.side)
            result = await coordinator.submit(
                execution_key=f"emergency-{row['confirmation_key']}-{index}", plan_id=None,
                telegram_id=telegram_id, account_id=int(row["account_id"]),
                exchange=str(row["exchange"]), mode=ExecutionMode.LIVE, request=request,
                readiness_passed=True, authority_source="LIVE_EMERGENCY_CLOSE",
                emergency_confirmation_id=int(row["id"]))
            submissions.append({"execution_id": result.execution_id, "state": result.state.value,
                                "symbol": position.symbol})
        remaining = [position for position in await adapter.positions() if position.quantity > 0]
        state = "COMPLETE" if not remaining else "SUBMITTED_RECONCILIATION_REQUIRED"
        result_payload = {"state": state, "submissions": submissions,
                          "remaining_positions": len(remaining), "reduce_only": True}
        with connect() as conn:
            conn.execute("""UPDATE live_emergency_confirmations SET state=?,result_json=?,
                completed_at=?,updated_at=? WHERE id=?""", (
                state, json.dumps(result_payload, sort_keys=True), now.isoformat(), now.isoformat(), row["id"]))
        LiveAuditRepository().record(
            event_type="LIVE_EMERGENCY_CLOSE", outcome=state, telegram_id=telegram_id,
            account_id=int(row["account_id"]), exchange=str(row["exchange"]), metadata=result_payload)
        return result_payload


class LiveCopyWorker:
    """Bounded dispatcher. Disabled by default and inert unless every LIVE gate is enabled."""

    worker_name = "live_copy_dispatcher"

    def __init__(self, *, adapter_factory: Callable[[int, str], ExchangeAdapter] | None = None,
                 batch_size: int | None = None, bot=None) -> None:
        self.enabled = os.getenv("LIVE_DISPATCHER_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        self.batch_size = max(1, min(int(batch_size or os.getenv("LIVE_DISPATCH_BATCH_SIZE", "10")), 25))
        self.worker_id = f"live-copy:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.adapter_factory = adapter_factory
        self.bot = bot
        self.queue = LiveExecutionQueueRepository()
        self.interval_seconds = max(30, min(int(os.getenv("LIVE_DISPATCH_INTERVAL_SECONDS", "60")), 600))
        self._stop = asyncio.Event()

    async def _alert(self, row: Mapping[str, Any], alert_type: str, details: Mapping[str, Any]) -> None:
        decision = IntelligenceAlertService().evaluate(
            int(row["telegram_id"]), symbol=str(row["exchange"]).upper(), timeframe="account",
            alert_type=alert_type, state_identity=f"{row['queue_key']}:{alert_type}",
            severity="CRITICAL" if alert_type in {"ORDER_REJECTED", "DAILY_LOSS_LIMIT_REACHED"} else "INFO",
            details=dict(details))
        if decision["status"] != "ELIGIBLE" or self.bot is None:
            return
        i18n = LocalizationService()
        language = i18n.language(int(row["telegram_id"]))
        try:
            await self.bot.send_message(
                int(row["telegram_id"]),
                f"⚠️ <b>{i18n.t('alert.live.title', language=language, exchange=i18n.market_token(row['exchange'], language=language))}</b>\n\n"
                + i18n.t("alert.live.body", language=language,
                         account=i18n.market_token(f"#{row['account_id']}", language=language),
                         reason=i18n.market_token(alert_type, language=language)),
                parse_mode="HTML")
        except Exception:
            IntelligenceAlertService.mark_delivery_failed(decision["alert_key"])
        else:
            IntelligenceAlertService.mark_delivered(decision["alert_key"])

    async def _recover_queue_row(self, row: Mapping[str, Any], adapter: ExchangeAdapter) -> str:
        coordinator = LiveExecutionCoordinator(adapter)
        execution_id = row.get("execution_id")
        if execution_id is None:
            with connect() as conn:
                execution = conn.execute("SELECT id,state FROM live_executions WHERE execution_key=?",
                                         (f"liveq-{row['queue_key']}",)).fetchone()
            if execution:
                execution_id = int(execution["id"])
        if execution_id is None:
            self.queue.schedule_retry(int(row["id"]), error_code="RECOVERY_CONFIRMED_PRE_SUBMIT")
            return "RETRY_WAIT"
        result = await coordinator.recover(int(execution_id))
        target = {
            LiveExecutionState.ACKNOWLEDGED: "ACKNOWLEDGED",
            LiveExecutionState.PARTIALLY_FILLED: "PARTIALLY_FILLED",
            LiveExecutionState.FILLED: "FILLED",
        }.get(result.state)
        if target:
            self.queue.transition(int(row["id"]), "RECOVERY_REQUIRED", target,
                                  execution_id=result.execution_id)
            return target
        return "RECOVERY_REQUIRED"

    def discover(self) -> int:
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT j.* FROM copy_execution_journal j
                JOIN live_exchange_accounts a ON a.id=j.exchange_account_id AND a.telegram_id=j.telegram_id
                JOIN live_copy_settings s ON s.account_id=a.id AND s.telegram_id=a.telegram_id
                WHERE j.status IN ('EXECUTING','EXECUTED') AND a.live_enabled=1
                  AND a.lifecycle_state='LIVE_ENABLED' AND s.enabled=1
                  AND NOT EXISTS(SELECT 1 FROM live_execution_queue q
                    WHERE q.account_id=a.id AND q.plan_id=j.plan_id)
                ORDER BY j.id LIMIT ?""", (self.batch_size,)).fetchall()]
        created = 0
        dispatcher = LiveCopyDispatcher(self.queue)
        for row in rows:
            try:
                # Admission uses the immutable decision-time intelligence snapshot, never a
                # user threshold copied from a profile and never a fresh/post-outcome value.
                with connect() as conn:
                    quality_row = conn.execute("""SELECT overall_quality,owner_telegram_id
                        FROM market_intelligence_snapshots WHERE signal_id=?
                        ORDER BY decision_at DESC,id DESC LIMIT 1""", (row["signal_id"],)).fetchone()
                quality = None
                if quality_row and (quality_row["owner_telegram_id"] in (None, 0)
                                    or int(quality_row["owner_telegram_id"]) == int(row["telegram_id"])):
                    quality = float(quality_row["overall_quality"])
                _, inserted = dispatcher.enqueue_journal(row, quality=quality)
                created += int(inserted)
            except (PermissionError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return created

    async def check_once(self) -> dict[str, Any]:
        if not self.enabled:
            runtime_finished(self.worker_name, processed=0, errors=0,
                             details={"state": "DISABLED_BY_DEFAULT"})
            return {"state": "DISABLED_BY_DEFAULT", "discovered": 0, "processed": 0, "errors": 0}
        if self.adapter_factory is None:
            return {"state": "BLOCKED", "code": "LIVE_ADAPTER_FACTORY_MISSING",
                    "discovered": 0, "processed": 0, "errors": 1}
        if not acquire_lease(self.worker_name, self.worker_id, max(self.interval_seconds * 2, 180)):
            return {"state": "LEASE_HELD", "discovered": 0, "processed": 0, "errors": 0}
        runtime_started(self.worker_name)
        discovered = self.discover()
        expired = self.queue.recover_expired()
        processed = errors = recovered = 0
        try:
            for pending in self.queue.recovery_required(self.batch_size):
                adapter = self.adapter_factory(int(pending["telegram_id"]), str(pending["exchange"]))
                try:
                    state = await self._recover_queue_row(pending, adapter)
                    recovered += int(state != "RECOVERY_REQUIRED")
                except Exception:
                    errors += 1
                finally:
                    await adapter.close()
            for pending in self.queue.due(self.batch_size):
                row, claimed = self.queue.claim(int(pending["id"]), worker_id=self.worker_id)
                if not claimed:
                    continue
                adapter = self.adapter_factory(int(row["telegram_id"]), str(row["exchange"]))
                try:
                    recovery = await LiveRecoveryService().recover(
                        adapter=adapter, telegram_id=int(row["telegram_id"]),
                        account_id=int(row["account_id"]), exchange=str(row["exchange"]))
                    if recovery.get("state") != "READY":
                        raise PermissionError(str(recovery.get("blocker_code") or "LIVE_RESTART_RECOVERY_REQUIRED"))
                    result = await LiveCopyDispatcher(self.queue).process_claimed(row, adapter=adapter)
                    processed += 1
                    event_type = {"ACKNOWLEDGED": "ORDER_SUBMITTED",
                                  "PARTIALLY_FILLED": "PARTIAL_FILL",
                                  "FILLED": "FULL_FILL", "REJECTED": "ORDER_REJECTED"}.get(
                                      str(result.get("state")))
                    if event_type:
                        await self._alert(row, event_type, result)
                    loss, limit = result.get("daily_loss_basis"), result.get("daily_loss_limit")
                    if loss is not None and limit and abs(min(float(loss), 0.0)) >= float(limit) * .8:
                        threshold_type = ("DAILY_LOSS_LIMIT_REACHED"
                                          if abs(min(float(loss), 0.0)) >= float(limit)
                                          else "DAILY_LOSS_APPROACHING")
                        await self._alert(row, threshold_type,
                                          {"loss_basis": loss, "limit": limit})
                except Exception as exc:
                    errors += 1
                    with connect() as conn:
                        current = conn.execute("SELECT state FROM live_execution_queue WHERE id=?",
                                               (row["id"],)).fetchone()
                    state = str(current["state"]) if current else "CLAIMED"
                    if state in {"CLAIMED", "SUBMITTING"}:
                        self.queue.transition(int(row["id"]), state, "RECOVERY_REQUIRED",
                                              error_code=str(exc)[:80])
                finally:
                    await adapter.close()
            state = "HEALTHY" if not errors else "DEGRADED"
            details = {"state": state, "discovered": discovered, "processed": processed,
                       "recovered": recovered, "expired_leases": expired}
            runtime_finished(self.worker_name, processed=processed + recovered, errors=errors,
                             details=details)
            return {**details, "errors": errors}
        finally:
            release_lease(self.worker_name, self.worker_id)

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            await self.check_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

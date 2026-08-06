from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from enum import StrEnum

from database.database import connect
from services.execution_models import ExecutionMode
from services.exchanges.base import (
    ExchangeAdapter, ExchangeError, ExchangeTimeoutError, ExchangeUnsupportedCapabilityError,
)
from services.exchanges.models import (
    ExchangeCapability, ExchangeFill, ExchangeOrder, ExchangeOrderRequest, SymbolRules,
)


class LiveExecutionState(StrEnum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    QUEUED = "QUEUED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


ALLOWED_LIVE_TRANSITIONS = {
    LiveExecutionState.CREATED: {LiveExecutionState.VALIDATED, LiveExecutionState.REJECTED},
    LiveExecutionState.VALIDATED: {LiveExecutionState.QUEUED, LiveExecutionState.REJECTED},
    LiveExecutionState.QUEUED: {LiveExecutionState.SUBMITTING, LiveExecutionState.CANCELLED},
    LiveExecutionState.SUBMITTING: {LiveExecutionState.SUBMITTED, LiveExecutionState.ACKNOWLEDGED,
                                    LiveExecutionState.UNKNOWN, LiveExecutionState.RETRY_WAIT,
                                    LiveExecutionState.REJECTED, LiveExecutionState.FAILED},
    LiveExecutionState.SUBMITTED: {LiveExecutionState.ACKNOWLEDGED, LiveExecutionState.UNKNOWN},
    LiveExecutionState.ACKNOWLEDGED: {LiveExecutionState.PARTIALLY_FILLED, LiveExecutionState.FILLED,
                                      LiveExecutionState.CANCEL_PENDING, LiveExecutionState.UNKNOWN},
    LiveExecutionState.PARTIALLY_FILLED: {LiveExecutionState.PARTIALLY_FILLED, LiveExecutionState.FILLED,
                                          LiveExecutionState.CANCEL_PENDING, LiveExecutionState.UNKNOWN},
    LiveExecutionState.CANCEL_PENDING: {LiveExecutionState.CANCELLED, LiveExecutionState.UNKNOWN},
    LiveExecutionState.RETRY_WAIT: {LiveExecutionState.SUBMITTING, LiveExecutionState.FAILED},
    LiveExecutionState.UNKNOWN: {LiveExecutionState.ACKNOWLEDGED, LiveExecutionState.PARTIALLY_FILLED,
                                  LiveExecutionState.FILLED, LiveExecutionState.RECOVERY_REQUIRED},
    LiveExecutionState.RECOVERY_REQUIRED: {LiveExecutionState.ACKNOWLEDGED,
                                            LiveExecutionState.PARTIALLY_FILLED,
                                            LiveExecutionState.FILLED},
}


def stable_client_order_id(execution_key: str, *, prefix: str = "lv") -> str:
    digest = hashlib.sha256(execution_key.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def request_checksum(request: ExchangeOrderRequest) -> str:
    payload = {
        "client_order_id": request.client_order_id, "symbol": request.symbol.upper(),
        "side": request.side.upper(), "order_type": request.order_type.upper(),
        "quantity": str(request.quantity), "price": str(request.price) if request.price is not None else None,
        "leverage": request.leverage, "margin_mode": request.margin_mode,
        "reduce_only": request.reduce_only, "stop_loss": str(request.stop_loss) if request.stop_loss else None,
        "take_profit": str(request.take_profit) if request.take_profit else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize_quantity(quantity: Decimal, rules: SymbolRules) -> Decimal:
    if quantity <= 0 or rules.quantity_step <= 0:
        raise ValueError("quantity and quantity step must be positive")
    normalized = (quantity / rules.quantity_step).to_integral_value(rounding=ROUND_DOWN) * rules.quantity_step
    if normalized < rules.min_quantity:
        raise ValueError("quantity below exchange minimum")
    return normalized


def validate_notional(quantity: Decimal, price: Decimal, rules: SymbolRules) -> None:
    if price <= 0:
        raise ValueError("reference price must be positive")
    if rules.min_notional is not None and quantity * price < rules.min_notional:
        raise ValueError("order notional below exchange minimum")


def safe_close_quantity(*, requested: Decimal, open_quantity: Decimal, side: str, position_side: str) -> Decimal:
    if requested <= 0 or open_quantity <= 0:
        raise ValueError("close quantity must be positive")
    expected = "SELL" if position_side.upper() in {"LONG", "BUY"} else "BUY"
    if side.upper() != expected:
        raise ValueError("close side would increase exposure")
    return min(requested, open_quantity)


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    execution_id: int
    state: LiveExecutionState
    client_order_id: str
    exchange_order_id: str | None = None


class LiveExecutionRepository:
    def create(self, *, execution_key: str, plan_id: str | None, telegram_id: int, account_id: int,
               exchange: str, mode: ExecutionMode, request: ExchangeOrderRequest) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""
                INSERT INTO live_executions(
                    execution_key,plan_id,telegram_id,account_id,exchange,mode,client_order_id,
                    symbol,side,order_type,quantity,price,reduce_only,state,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(execution_key) DO NOTHING
            """, (execution_key, plan_id, telegram_id, account_id, exchange, mode.value,
                  request.client_order_id, request.symbol, request.side, request.order_type,
                  float(request.quantity), float(request.price) if request.price is not None else None,
                  int(request.reduce_only), LiveExecutionState.CREATED.value, now, now))
            row = conn.execute("SELECT * FROM live_executions WHERE execution_key=?", (execution_key,)).fetchone()
        return dict(row)

    def transition(self, execution_id: int, expected: LiveExecutionState, target: LiveExecutionState,
                   *, exchange_order_id: str | None = None, recovery_reason: str | None = None,
                   next_retry_at: str | None = None) -> bool:
        if target not in ALLOWED_LIVE_TRANSITIONS.get(expected, set()):
            raise ValueError(f"invalid live transition {expected}->{target}")
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            cur = conn.execute("""
                UPDATE live_executions SET state=?, exchange_order_id=COALESCE(?,exchange_order_id),
                    recovery_reason=?, next_retry_at=?, version=version+1, updated_at=?
                WHERE id=? AND state=?
            """, (target.value, exchange_order_id, recovery_reason, next_retry_at, now,
                  execution_id, expected.value))
        return cur.rowcount == 1

    def get(self, execution_id: int) -> dict | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM live_executions WHERE id=?", (execution_id,)).fetchone()
        return dict(row) if row else None

    def begin_attempt(self, execution: dict, checksum: str) -> int:
        with connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(attempt_number),0)+1 AS n FROM live_execution_attempts WHERE execution_id=?",
                               (execution["id"],)).fetchone()
            number = int(row["n"])
            conn.execute("""
                INSERT INTO live_execution_attempts(execution_id,attempt_number,client_order_id,adapter,
                    account_id,request_checksum,status,started_at) VALUES(?,?,?,?,?,?,?,?)
            """, (execution["id"], number, execution["client_order_id"], execution["exchange"],
                  execution["account_id"], checksum, "STARTED", datetime.now(timezone.utc).isoformat()))
        return number

    def finish_attempt(self, execution_id: int, attempt: int, *, status: str,
                       order: ExchangeOrder | None = None, error: ExchangeError | None = None,
                       retry_at: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        response_hash = None
        if order:
            safe = {"order_id": order.order_id, "client_order_id": order.client_order_id,
                    "status": order.status, "executed_quantity": str(order.executed_quantity)}
            response_hash = hashlib.sha256(json.dumps(safe, sort_keys=True).encode()).hexdigest()
        with connect() as conn:
            conn.execute("""
                UPDATE live_execution_attempts SET status=?,reason=?,exchange_order_id=?,normalized_error_code=?,
                    normalized_error=?,raw_response_checksum=?,retry_at=?,completed_at=?
                WHERE execution_id=? AND attempt_number=?
            """, (status, error.code if error else status, order.order_id if order else None,
                  error.code if error else None,
                  str(error) if error else None, response_hash, retry_at, now, execution_id, attempt))

    def ingest_fills(self, execution: dict, fills: list[ExchangeFill]) -> tuple[Decimal, Decimal, Decimal]:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            for fill in fills:
                conn.execute("""
                    INSERT INTO live_execution_fills(execution_id,account_id,exchange_fill_id,exchange_order_id,
                        quantity,price,commission,commission_asset,filled_at,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,exchange_fill_id) DO NOTHING
                """, (execution["id"], execution["account_id"], fill.fill_id, fill.order_id,
                      float(fill.quantity), float(fill.price), float(fill.commission), fill.commission_asset,
                      str(fill.filled_at_ms) if fill.filled_at_ms else None, now))
            rows = conn.execute("SELECT quantity,price,commission FROM live_execution_fills WHERE execution_id=?",
                                (execution["id"],)).fetchall()
            qty = sum((Decimal(str(r["quantity"])) for r in rows), Decimal("0"))
            commission = sum((Decimal(str(r["commission"])) for r in rows), Decimal("0"))
            average = (sum((Decimal(str(r["quantity"])) * Decimal(str(r["price"])) for r in rows), Decimal("0")) / qty
                       if qty else Decimal("0"))
            conn.execute("UPDATE live_executions SET executed_quantity=?,average_fill_price=?,commission=?,updated_at=? WHERE id=?",
                         (float(qty), float(average) if qty else None, float(commission), now, execution["id"]))
        return qty, average, commission


class LiveExecutionCoordinator:
    def __init__(self, adapter: ExchangeAdapter, repository: LiveExecutionRepository | None = None,
                 *, max_attempts: int = 3) -> None:
        self.adapter = adapter
        self.repository = repository or LiveExecutionRepository()
        self.max_attempts = max_attempts

    def _require(self, capability: ExchangeCapability) -> None:
        if not self.adapter.capabilities().supports(capability):
            raise ExchangeUnsupportedCapabilityError(f"adapter does not support {capability.value}")

    async def safe_close_request(self, request: ExchangeOrderRequest) -> ExchangeOrderRequest:
        if not request.reduce_only:
            raise ValueError("safe close request must be reduce-only")
        self._require(ExchangeCapability.REDUCE_ONLY)
        self._require(ExchangeCapability.POSITIONS)
        canonical = lambda value: "".join(char for char in value.upper() if char.isalnum())
        matches = [position for position in await self.adapter.positions()
                   if canonical(position.symbol) == canonical(request.symbol) and position.quantity > 0]
        if len(matches) != 1:
            raise ValueError("safe close requires exactly one resolved exchange position")
        position = matches[0]
        quantity = safe_close_quantity(requested=request.quantity, open_quantity=position.quantity,
                                       side=request.side, position_side=position.side)
        return replace(request, quantity=quantity, reduce_only=True)

    async def submit(self, *, execution_key: str, plan_id: str | None, telegram_id: int,
                     account_id: int, exchange: str, mode: ExecutionMode,
                     request: ExchangeOrderRequest, readiness_passed: bool = False) -> SubmissionResult:
        if mode is ExecutionMode.LIVE and not readiness_passed:
            raise PermissionError("LIVE_READINESS_REQUIRED")
        if mode is ExecutionMode.LIVE and exchange.lower() == "bingx":
            environment = str(getattr(self.adapter, "environment", ""))
            if environment == "prod-live":
                limits = self._require_durable_bingx_live_gate(account_id)
                if request.leverage > int(limits["max_leverage"]):
                    raise PermissionError("BINGX_MAX_LEVERAGE_EXCEEDED")
                if not request.reduce_only:
                    if request.price is None or request.price <= 0:
                        raise PermissionError("BINGX_REFERENCE_PRICE_REQUIRED")
                    order_notional = request.quantity * request.price
                    if order_notional > Decimal(str(limits["max_order_notional"])):
                        raise PermissionError("BINGX_MAX_ORDER_NOTIONAL_EXCEEDED")
                    positions = await self.adapter.positions()
                    exposure = Decimal("0")
                    for position in positions:
                        if position.quantity > 0 and position.mark_price <= 0:
                            raise PermissionError("BINGX_POSITION_MARK_PRICE_UNRESOLVED")
                        exposure += position.quantity * position.mark_price
                    if exposure + order_notional > Decimal(str(limits["max_account_exposure"])):
                        raise PermissionError("BINGX_MAX_ACCOUNT_EXPOSURE_EXCEEDED")
            elif environment == "prod-vst":
                if os.getenv("BINGX_VST_CERTIFICATION_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
                    raise PermissionError("BINGX_VST_EXECUTION_DISABLED")
            else:
                raise PermissionError("BINGX_ENVIRONMENT_AMBIGUOUS")
        if mode in {ExecutionMode.LIVE_DRY_RUN, ExecutionMode.LIVE}:
            if request.stop_loss is not None:
                self._require(ExchangeCapability.STOP_LOSS)
            if request.take_profit is not None:
                self._require(ExchangeCapability.TAKE_PROFIT)
            if request.reduce_only:
                request = await self.safe_close_request(request)
        execution = self.repository.create(execution_key=execution_key, plan_id=plan_id,
                                           telegram_id=telegram_id, account_id=account_id,
                                           exchange=exchange, mode=mode, request=request)
        state = LiveExecutionState(execution["state"])
        if mode in {ExecutionMode.DISABLED, ExecutionMode.PAPER, ExecutionMode.SHADOW}:
            return SubmissionResult(execution["id"], state, request.client_order_id)
        if mode is ExecutionMode.LIVE_DRY_RUN:
            await self.adapter.health()
            await self.adapter.symbol_rules(request.symbol)
            return SubmissionResult(execution["id"], state, request.client_order_id)
        if state not in {LiveExecutionState.CREATED, LiveExecutionState.RETRY_WAIT}:
            return SubmissionResult(execution["id"], state, request.client_order_id, execution.get("exchange_order_id"))
        if state is LiveExecutionState.RETRY_WAIT and execution.get("next_retry_at"):
            retry_at = datetime.fromisoformat(str(execution["next_retry_at"]).replace("Z", "+00:00"))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < retry_at:
                return SubmissionResult(execution["id"], state, request.client_order_id)
        if state is LiveExecutionState.CREATED:
            won = self.repository.transition(execution["id"], state, LiveExecutionState.VALIDATED)
            won = won and self.repository.transition(execution["id"], LiveExecutionState.VALIDATED,
                                                      LiveExecutionState.QUEUED)
            source = LiveExecutionState.QUEUED
        else:
            won = True
            source = state
        won = won and self.repository.transition(execution["id"], source, LiveExecutionState.SUBMITTING)
        if not won:
            current = self.repository.get(execution["id"])
            return SubmissionResult(execution["id"], LiveExecutionState(current["state"]),
                                    request.client_order_id, current.get("exchange_order_id"))
        execution = self.repository.get(execution["id"])
        attempt = self.repository.begin_attempt(execution, request_checksum(request))
        try:
            order = await self.adapter.place_order(request)
        except ExchangeTimeoutError as exc:
            self.repository.finish_attempt(execution["id"], attempt, status="AMBIGUOUS", error=exc)
            self.repository.transition(execution["id"], LiveExecutionState.SUBMITTING,
                                       LiveExecutionState.UNKNOWN, recovery_reason="SUBMISSION_TIMEOUT")
            return SubmissionResult(execution["id"], LiveExecutionState.UNKNOWN, request.client_order_id)
        except ExchangeError as exc:
            retryable = exc.retryable and attempt < self.max_attempts and not exc.ambiguous_submission
            retry_at = (datetime.now(timezone.utc) + timedelta(seconds=2 ** attempt)).isoformat() if retryable else None
            target = LiveExecutionState.RETRY_WAIT if retryable else LiveExecutionState.FAILED
            self.repository.finish_attempt(execution["id"], attempt, status=target.value, error=exc, retry_at=retry_at)
            self.repository.transition(execution["id"], LiveExecutionState.SUBMITTING, target,
                                       next_retry_at=retry_at)
            return SubmissionResult(execution["id"], target, request.client_order_id)
        self.repository.finish_attempt(execution["id"], attempt, status="ACKNOWLEDGED", order=order)
        self.repository.transition(execution["id"], LiveExecutionState.SUBMITTING,
                                   LiveExecutionState.ACKNOWLEDGED, exchange_order_id=order.order_id)
        return SubmissionResult(execution["id"], LiveExecutionState.ACKNOWLEDGED,
                                request.client_order_id, order.order_id)

    @staticmethod
    def _require_durable_bingx_live_gate(account_id: int) -> dict:
        enabled = os.getenv("LIVE_EXECUTION_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        allowed = os.getenv("BINGX_PRODUCTION_ADAPTER_ALLOWED", "false").lower() in {"1", "true", "yes", "on"}
        deployment = os.getenv("ENVIRONMENT", "").lower() in {"production", "render"}
        if not (enabled and allowed and deployment):
            raise PermissionError("BINGX_PRODUCTION_ENVIRONMENT_GATE_FAILED")
        now = datetime.now(timezone.utc)
        with connect() as conn:
            account = conn.execute("SELECT * FROM live_exchange_accounts WHERE id=?", (account_id,)).fetchone()
            cert = conn.execute("""
                SELECT status,environment,expires_at FROM bingx_certification_audits
                WHERE account_id=? AND certification_type='VST_ECONOMIC'
                ORDER BY started_at DESC LIMIT 1
            """, (account_id,)).fetchone()
            unresolved = conn.execute("""
                SELECT COUNT(*) AS n FROM live_executions
                WHERE account_id=? AND state IN ('UNKNOWN','RECOVERY_REQUIRED')
            """, (account_id,)).fetchone()
        if not account or not bool(account["live_enabled"]) or not account["confirmed_at"] or bool(account["kill_switch"]):
            raise PermissionError("BINGX_ACCOUNT_LIVE_GATE_FAILED")
        if not account["max_order_notional"] or not account["max_account_exposure"] or not account["max_leverage"]:
            raise PermissionError("BINGX_ACCOUNT_LIMITS_MISSING")
        if int(unresolved["n"] or 0):
            raise PermissionError("BINGX_RECOVERY_REQUIRED")
        if not cert or cert["status"] != "VST_ECONOMIC_PASSED" or cert["environment"] != "prod-vst" or not cert["expires_at"]:
            raise PermissionError("BINGX_VST_CERTIFICATION_REQUIRED")
        expires = datetime.fromisoformat(str(cert["expires_at"]).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= now:
            raise PermissionError("BINGX_VST_CERTIFICATION_EXPIRED")
        return dict(account)

    async def recover(self, execution_id: int) -> SubmissionResult:
        execution = self.repository.get(execution_id)
        if not execution:
            raise KeyError(execution_id)
        state = LiveExecutionState(execution["state"])
        if state not in {LiveExecutionState.UNKNOWN, LiveExecutionState.RECOVERY_REQUIRED}:
            return SubmissionResult(execution_id, state, execution["client_order_id"], execution.get("exchange_order_id"))
        order = await self.adapter.query_order_by_client_id(
            symbol=execution["symbol"], client_order_id=execution["client_order_id"])
        if order is None:
            if state is LiveExecutionState.UNKNOWN:
                self.repository.transition(execution_id, state, LiveExecutionState.RECOVERY_REQUIRED,
                                           recovery_reason="EXCHANGE_TRUTH_UNAVAILABLE")
            return SubmissionResult(execution_id, LiveExecutionState.RECOVERY_REQUIRED, execution["client_order_id"])
        fills = await self.adapter.fills(symbol=execution["symbol"], order_id=order.order_id)
        qty, _, _ = self.repository.ingest_fills(execution, fills)
        requested = Decimal(str(execution["quantity"]))
        target = LiveExecutionState.FILLED if qty >= requested else (
            LiveExecutionState.PARTIALLY_FILLED if qty > 0 else LiveExecutionState.ACKNOWLEDGED)
        self.repository.transition(execution_id, state, target, exchange_order_id=order.order_id)
        return SubmissionResult(execution_id, target, execution["client_order_id"], order.order_id)

    async def cancel(self, execution_id: int) -> SubmissionResult:
        execution = self.repository.get(execution_id)
        if not execution:
            raise KeyError(execution_id)
        state = LiveExecutionState(execution["state"])
        if state not in {LiveExecutionState.ACKNOWLEDGED, LiveExecutionState.PARTIALLY_FILLED}:
            return SubmissionResult(execution_id, state, execution["client_order_id"],
                                    execution.get("exchange_order_id"))
        self._require(ExchangeCapability.CANCEL_ORDER)
        if not self.repository.transition(execution_id, state, LiveExecutionState.CANCEL_PENDING):
            current = self.repository.get(execution_id)
            return SubmissionResult(execution_id, LiveExecutionState(current["state"]),
                                    execution["client_order_id"], current.get("exchange_order_id"))
        try:
            order = await self.adapter.cancel_order(symbol=execution["symbol"],
                                                    order_id=execution["exchange_order_id"])
        except ExchangeTimeoutError:
            self.repository.transition(execution_id, LiveExecutionState.CANCEL_PENDING,
                                       LiveExecutionState.UNKNOWN,
                                       recovery_reason="CANCELLATION_TIMEOUT")
            return SubmissionResult(execution_id, LiveExecutionState.UNKNOWN,
                                    execution["client_order_id"], execution.get("exchange_order_id"))
        self.repository.transition(execution_id, LiveExecutionState.CANCEL_PENDING,
                                   LiveExecutionState.CANCELLED, exchange_order_id=order.order_id)
        return SubmissionResult(execution_id, LiveExecutionState.CANCELLED,
                                execution["client_order_id"], order.order_id)

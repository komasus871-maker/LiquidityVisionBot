from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from services.exchanges.base import ExchangeConfigurationError, ExchangeRequestError
from services.exchanges.manager import ExchangeManager
from services.exchanges.models import ExchangeName, ExchangeOrder
from services.exchanges.registry import ExchangeRegistry
from services.exchanges.safety import ExecutionSafetyPolicy, ExecutionSafetyValidator, OrderIntent, OrderSide


class ExecutionState(StrEnum):
    CREATED = "created"
    PREFLIGHT_REJECTED = "preflight_rejected"
    QUEUED = "queued"
    SENT = "sent"
    ACCEPTED = "accepted"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class DemoOrderRequest:
    exchange: ExchangeName
    symbol: str
    side: OrderSide
    order_type: str
    quantity: Decimal
    reference_price: Decimal
    leverage: int
    limit_price: Decimal | None = None
    reduce_only: bool = False
    position_side: str | None = None
    client_order_id: str | None = None

    def idempotency_key(self) -> str:
        if self.client_order_id:
            return self.client_order_id
        raw = "|".join((
            self.exchange.value, self.symbol.upper(), self.side.value, self.order_type.upper(),
            str(self.quantity), str(self.limit_price or ""), str(self.leverage),
            str(self.reduce_only), str(self.position_side or ""),
        ))
        return "lv-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    state: ExecutionState
    exchange: ExchangeName
    client_order_id: str
    order: ExchangeOrder | None = None
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str | None = None
    latency_ms: float | None = None


class ExecutionAuditLog:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("EXECUTION_AUDIT_PATH", "data/execution_audit.jsonl"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def write(self, event: str, **payload: Any) -> None:
        record = {"timestamp_ms": int(time.time() * 1000), "event": event, **payload}
        line = json.dumps(record, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
        def _append() -> None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        async with self._lock:
            await asyncio.to_thread(_append)


class ExecutionCircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, cooldown_seconds: float = 60.0) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1.0, cooldown_seconds)
        self.failures = 0
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if time.monotonic() - self.opened_at >= self.cooldown_seconds:
            self.failures = 0
            self.opened_at = None
            return True
        return False

    def success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()


class DemoExecutionManager:
    """Serialized, idempotent demo execution. Live accounts are rejected before adapter writes."""

    def __init__(self, registry: ExchangeRegistry, *, timeout_seconds: float = 25.0) -> None:
        self.registry = registry
        self.account_manager = ExchangeManager(registry, operation_timeout_seconds=timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self.audit = ExecutionAuditLog()
        self._lock = asyncio.Lock()
        self._receipts: dict[str, ExecutionReceipt] = {}
        self._breaker: dict[ExchangeName, ExecutionCircuitBreaker] = {}
        self._runtime_killed = False

    @property
    def enabled(self) -> bool:
        raw = os.getenv("DEMO_EXECUTION_ENABLED", "false").strip().lower()
        return raw in {"1", "true", "yes", "on"} and not self._runtime_killed

    def kill(self) -> None:
        self._runtime_killed = True

    def resume(self) -> None:
        self._runtime_killed = False

    async def submit(self, request: DemoOrderRequest) -> ExecutionReceipt:
        key = request.idempotency_key()
        async with self._lock:
            if key in self._receipts:
                return self._receipts[key]
            if not self.enabled:
                raise ExchangeConfigurationError("demo execution kill switch is active; set DEMO_EXECUTION_ENABLED=true")
            breaker = self._breaker.setdefault(
                request.exchange,
                ExecutionCircuitBreaker(
                    failure_threshold=int(os.getenv("EXECUTION_CIRCUIT_FAILURES", "3")),
                    cooldown_seconds=float(os.getenv("EXECUTION_CIRCUIT_COOLDOWN", "60")),
                ),
            )
            if not breaker.allow():
                raise ExchangeRequestError("execution circuit breaker is open")

            adapter = self.registry.create(request.exchange)
            started = time.perf_counter()
            try:
                health = await asyncio.wait_for(adapter.health(), timeout=self.timeout_seconds)
                if not health.authenticated or not health.testnet:
                    raise ExchangeConfigurationError("demo execution requires authenticated testnet/demo credentials")
                rules = await asyncio.wait_for(adapter.symbol_rules(request.symbol), timeout=self.timeout_seconds)
                snapshot = await self.account_manager.snapshot(request.exchange, symbol=request.symbol)
                intent = OrderIntent(
                    exchange=request.exchange,
                    symbol=request.symbol,
                    side=request.side,
                    quantity=request.quantity,
                    reference_price=request.reference_price,
                    leverage=request.leverage,
                    reduce_only=request.reduce_only,
                    demo=True,
                )
                decision = ExecutionSafetyValidator(ExecutionSafetyPolicy.from_env()).validate(
                    intent, rules, positions=snapshot.positions, open_orders=snapshot.open_orders
                )
                if not decision.approved:
                    receipt = ExecutionReceipt(
                        ExecutionState.PREFLIGHT_REJECTED, request.exchange, key,
                        violations=decision.violations, warnings=decision.warnings,
                    )
                    self._receipts[key] = receipt
                    await self.audit.write("preflight_rejected", client_order_id=key, violations=decision.violations)
                    return receipt

                await self.audit.write("intent_queued", client_order_id=key, request=asdict(request))
                order = await asyncio.wait_for(
                    adapter.create_demo_order(
                        symbol=request.symbol,
                        side=request.side.value,
                        order_type=request.order_type,
                        quantity=request.quantity,
                        price=request.limit_price,
                        leverage=request.leverage,
                        reduce_only=request.reduce_only,
                        position_side=request.position_side,
                        client_order_id=key,
                    ),
                    timeout=self.timeout_seconds,
                )
                breaker.success()
                receipt = ExecutionReceipt(
                    ExecutionState.ACCEPTED, request.exchange, key, order=order,
                    warnings=decision.warnings,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                )
                self._receipts[key] = receipt
                await self.audit.write("order_accepted", client_order_id=key, order=asdict(order), latency_ms=receipt.latency_ms)
                return receipt
            except Exception as exc:
                breaker.failure()
                await self.audit.write("order_failed", client_order_id=key, error=f"{type(exc).__name__}: {exc}")
                raise
            finally:
                await adapter.close()

    async def cancel(self, exchange: ExchangeName, symbol: str, order_id: str) -> ExchangeOrder:
        if not self.enabled:
            raise ExchangeConfigurationError("demo execution kill switch is active")
        adapter = self.registry.create(exchange)
        try:
            health = await adapter.health()
            if not health.authenticated or not health.testnet:
                raise ExchangeConfigurationError("demo cancellation requires authenticated testnet/demo credentials")
            order = await adapter.cancel_demo_order(symbol=symbol, order_id=order_id)
            await self.audit.write("order_cancelled", exchange=exchange.value, symbol=symbol, order_id=order_id)
            return order
        finally:
            await adapter.close()

    async def status(self, exchange: ExchangeName, symbol: str, order_id: str) -> ExchangeOrder:
        adapter = self.registry.create(exchange)
        try:
            return await adapter.demo_order_status(symbol=symbol, order_id=order_id)
        finally:
            await adapter.close()

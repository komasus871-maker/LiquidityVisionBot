from __future__ import annotations

import hashlib
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from database.database import connect
from services.exchanges.bingx_swap import BingXSwapAdapter, bingx_client_order_id
from services.exchanges.models import ExchangeOrderRequest
from services.execution_models import ExecutionMode
from services.live_execution import LiveExecutionCoordinator, LiveExecutionState, stable_client_order_id


@dataclass(frozen=True, slots=True)
class BingXCertificationReport:
    run_key: str
    adapter_version: str
    environment: str
    certification_type: str
    status: str
    credential_status: str
    trading_permission: bool
    withdrawal_permission: str
    capabilities: tuple[str, ...]
    server_time_drift_ms: int
    account_mode: str
    margin_mode: str
    balance_assets: int
    available_funds: str
    open_positions: int
    open_orders: int
    symbol: str
    price_tick: str
    quantity_step: str
    min_quantity: str
    min_notional: str | None
    requested_quantity: str
    normalized_quantity: str | None
    requested_price: str
    normalized_price: str | None
    readiness_blockers: tuple[str, ...]
    order_submission_calls: int
    timestamp: str
    expires_at: str | None


class BingXCertificationService:
    """Runs authenticated read-only certification; it cannot submit economic orders."""

    def __init__(self, adapter: BingXSwapAdapter) -> None:
        self.adapter = adapter

    async def dry_run(self, *, telegram_id: int, account_id: int, symbol: str,
                      sample_quantity: Decimal, sample_price: Decimal,
                      expected_environment: str | None = None) -> BingXCertificationReport:
        started = datetime.now(timezone.utc)
        environment = self.adapter.environment
        run_key = hashlib.sha256(
            f"{account_id}:{environment}:{symbol}:{started.isoformat()}".encode()).hexdigest()
        blockers: list[str] = []
        if expected_environment and environment != expected_environment:
            blockers.append("ENVIRONMENT_MISMATCH")
        server_ms = await self.adapter.server_time()
        drift = server_ms - int(time.time() * 1000)
        account = await self.adapter.account_info()
        balances = await self.adapter.balances()
        positions = await self.adapter.positions()
        orders = await self.adapter.open_orders(symbol)
        rules = await self.adapter.symbol_rules(symbol)
        requested = ExchangeOrderRequest(
            symbol=symbol, side="BUY", order_type="LIMIT", quantity=sample_quantity,
            price=sample_price, leverage=1, client_order_id=bingx_client_order_id(run_key),
            position_side="LONG" if account.position_mode == "HEDGE" else "BOTH",
        )
        normalized = None
        try:
            normalized = await self.adapter.normalize_order(requested)
        except Exception as exc:
            blockers.append(getattr(exc, "code", "ORDER_NORMALIZATION_FAILED"))
        if abs(drift) > int(os.getenv("BINGX_MAX_SERVER_DRIFT_MS", "1500")):
            blockers.append("SERVER_TIME_DRIFT")
        capabilities = tuple(sorted(item.value for item in self.adapter.capabilities().supported))
        required = {
            "account_sync", "balances", "symbol_rules", "place_order", "cancel_order",
            "query_order", "query_by_client_id", "open_orders", "fills", "positions",
            "reduce_only", "server_time",
        }
        if not required.issubset(capabilities):
            blockers.append("CAPABILITY_INCOMPLETE")
        # A read-only report is useful for LIVE_DRY_RUN but never authorizes LIVE.
        status = "DRY_RUN_PASSED" if not blockers else "DRY_RUN_BLOCKED"
        expires = started + timedelta(hours=max(1, int(os.getenv("BINGX_CERTIFICATION_TTL_HOURS", "24"))))
        available = sum((balance.available_balance for balance in balances), Decimal("0"))
        margin_modes = {position.margin_mode for position in positions if position.margin_mode}
        margin_mode = next(iter(margin_modes)) if len(margin_modes) == 1 else ("MIXED" if margin_modes else "UNKNOWN")
        report = BingXCertificationReport(
            run_key=run_key, adapter_version=self.adapter.ADAPTER_VERSION, environment=environment,
            certification_type="LIVE_DRY_RUN", status=status, credential_status="VALID",
            trading_permission=account.trading_enabled,
            withdrawal_permission="UNKNOWN_NOT_REQUIRED" if account.withdrawal_enabled is None else str(account.withdrawal_enabled).upper(),
            capabilities=capabilities, server_time_drift_ms=drift,
            account_mode=account.position_mode or "UNKNOWN", margin_mode=margin_mode,
            balance_assets=len(balances), available_funds=str(available),
            open_positions=len(positions), open_orders=len(orders), symbol=rules.symbol,
            price_tick=str(rules.price_tick), quantity_step=str(rules.quantity_step),
            min_quantity=str(rules.min_quantity),
            min_notional=str(rules.min_notional) if rules.min_notional is not None else None,
            requested_quantity=str(sample_quantity),
            normalized_quantity=str(normalized.quantity) if normalized else None,
            requested_price=str(sample_price), normalized_price=str(normalized.price) if normalized else None,
            readiness_blockers=tuple(blockers) + ("ECONOMIC_VST_CERTIFICATION_REQUIRED",),
            order_submission_calls=0, timestamp=started.isoformat(), expires_at=expires.isoformat(),
        )
        self._persist(telegram_id=telegram_id, account_id=account_id, report=report,
                      permissions={"trading": account.trading_enabled,
                                   "withdrawal": account.withdrawal_enabled})
        self._cache_rules(account_id, rules, expires)
        return report

    async def certify_vst_economic(self, *, telegram_id: int, account_id: int, symbol: str,
                                   quantity: Decimal, reference_price: Decimal,
                                   confirmation: str) -> BingXCertificationReport:
        if self.adapter.environment != "prod-vst" or not self.adapter.credentials.testnet:
            raise PermissionError("BINGX_VST_ENVIRONMENT_REQUIRED")
        if os.getenv("BINGX_VST_CERTIFICATION_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
            raise PermissionError("BINGX_VST_CERTIFICATION_DISABLED")
        if confirmation != "CERTIFY_VST":
            raise PermissionError("BINGX_VST_CONFIRMATION_REQUIRED")
        base = await self.dry_run(
            telegram_id=telegram_id, account_id=account_id, symbol=symbol,
            sample_quantity=quantity, sample_price=reference_price, expected_environment="prod-vst")
        if base.status != "DRY_RUN_PASSED":
            return base
        account = await self.adapter.account_info()
        position_side = "LONG" if account.position_mode == "HEDGE" else "BOTH"
        economic_key = f"bingx-vst-cert:{base.run_key}"
        running = replace(
            base, run_key=hashlib.sha256(f"{base.run_key}:economic".encode()).hexdigest(),
            certification_type="VST_ECONOMIC", status="VST_ECONOMIC_RUNNING",
            readiness_blockers=("CERTIFICATION_IN_PROGRESS",), order_submission_calls=0,
        )
        self._persist(telegram_id=telegram_id, account_id=account_id, report=running,
                      permissions={"trading": True, "withdrawal": None})
        entry_request = ExchangeOrderRequest(
            symbol=symbol, side="BUY", order_type="MARKET", quantity=quantity,
            price=reference_price, leverage=1, position_side=position_side,
            client_order_id=stable_client_order_id(economic_key),
        )
        coordinator = LiveExecutionCoordinator(self.adapter, max_attempts=1)
        entry = await coordinator.submit(
            execution_key=economic_key, plan_id=None, telegram_id=telegram_id,
            account_id=account_id, exchange="bingx", mode=ExecutionMode.LIVE,
            request=entry_request, readiness_passed=True, authority_source="VST_CERTIFICATION",
        )
        if entry.state is not LiveExecutionState.ACKNOWLEDGED:
            return self._economic_report(telegram_id, account_id, base, "VST_ENTRY_NOT_ACKNOWLEDGED", 1)
        entry_execution = coordinator.repository.get(entry.execution_id)
        entry_fills = []
        for _ in range(5):
            entry_fills = await self.adapter.fills(symbol=symbol, order_id=entry.exchange_order_id)
            if entry_fills:
                break
            await asyncio.sleep(0.5)
        if not entry_fills:
            return self._economic_report(telegram_id, account_id, base, "VST_ENTRY_FILL_UNVERIFIED", 1)
        filled_qty, _, _ = coordinator.repository.ingest_fills(entry_execution, entry_fills)
        target = LiveExecutionState.FILLED if filled_qty >= Decimal(str(entry_execution["quantity"])) else LiveExecutionState.PARTIALLY_FILLED
        coordinator.repository.transition(entry.execution_id, LiveExecutionState.ACKNOWLEDGED, target,
                                          exchange_order_id=entry.exchange_order_id)
        close_side = "SELL"
        close_key = f"{economic_key}:close"
        close_request = ExchangeOrderRequest(
            symbol=symbol, side=close_side, order_type="MARKET", quantity=filled_qty,
            price=reference_price, leverage=1, position_side=position_side,
            reduce_only=True, client_order_id=stable_client_order_id(close_key),
        )
        close = await coordinator.submit(
            execution_key=close_key, plan_id=None, telegram_id=telegram_id,
            account_id=account_id, exchange="bingx", mode=ExecutionMode.LIVE,
            request=close_request, readiness_passed=True, authority_source="VST_CERTIFICATION",
        )
        if close.state is not LiveExecutionState.ACKNOWLEDGED:
            return self._economic_report(telegram_id, account_id, base, "VST_CLOSE_NOT_ACKNOWLEDGED", 2)
        close_execution = coordinator.repository.get(close.execution_id)
        close_fills = []
        for _ in range(5):
            close_fills = await self.adapter.fills(symbol=symbol, order_id=close.exchange_order_id)
            if close_fills:
                break
            await asyncio.sleep(0.5)
        if not close_fills:
            return self._economic_report(telegram_id, account_id, base, "VST_CLOSE_FILL_UNVERIFIED", 2)
        close_qty, _, _ = coordinator.repository.ingest_fills(close_execution, close_fills)
        close_target = LiveExecutionState.FILLED if close_qty >= filled_qty else LiveExecutionState.PARTIALLY_FILLED
        coordinator.repository.transition(close.execution_id, LiveExecutionState.ACKNOWLEDGED, close_target,
                                          exchange_order_id=close.exchange_order_id)
        positions = [item for item in await self.adapter.positions()
                     if item.symbol.replace("-", "").upper() == symbol.replace("-", "").upper() and item.quantity > 0]
        blocker = None if not positions and close_target is LiveExecutionState.FILLED else "VST_ZERO_EXPOSURE_UNVERIFIED"
        return self._economic_report(telegram_id, account_id, base, blocker, 2)

    def _economic_report(self, telegram_id: int, account_id: int, base: BingXCertificationReport,
                         blocker: str | None, calls: int) -> BingXCertificationReport:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=max(1, int(os.getenv("BINGX_CERTIFICATION_TTL_HOURS", "24"))))
        report = replace(
            base, run_key=hashlib.sha256(f"{base.run_key}:economic".encode()).hexdigest(),
            certification_type="VST_ECONOMIC", status="VST_ECONOMIC_PASSED" if blocker is None else "VST_ECONOMIC_BLOCKED",
            readiness_blockers=() if blocker is None else (blocker,), order_submission_calls=calls,
            timestamp=now.isoformat(), expires_at=expires.isoformat(),
        )
        self._persist(telegram_id=telegram_id, account_id=account_id, report=report,
                      permissions={"trading": True, "withdrawal": None})
        return report

    def _persist(self, *, telegram_id: int, account_id: int, report: BingXCertificationReport,
                 permissions: dict[str, object]) -> None:
        payload = json.dumps(asdict(report), sort_keys=True)
        with connect() as conn:
            conn.execute("""
                INSERT INTO bingx_certification_audits(
                    run_key,telegram_id,account_id,environment,adapter_version,certification_type,status,
                    symbol,capability_snapshot_json,permission_snapshot_json,report_json,
                    server_time_drift_ms,account_mode,margin_mode,started_at,completed_at,expires_at,error_code
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_key) DO UPDATE SET
                    status=excluded.status,capability_snapshot_json=excluded.capability_snapshot_json,
                    permission_snapshot_json=excluded.permission_snapshot_json,report_json=excluded.report_json,
                    server_time_drift_ms=excluded.server_time_drift_ms,account_mode=excluded.account_mode,
                    margin_mode=excluded.margin_mode,completed_at=excluded.completed_at,
                    expires_at=excluded.expires_at,error_code=excluded.error_code
            """, (report.run_key, telegram_id, account_id, report.environment, report.adapter_version,
                  report.certification_type, report.status, report.symbol, json.dumps(report.capabilities),
                  json.dumps(permissions, sort_keys=True), payload, report.server_time_drift_ms,
                  report.account_mode, report.margin_mode, report.timestamp, report.timestamp,
                  report.expires_at, report.readiness_blockers[0] if report.readiness_blockers else None))
            conn.execute("""
                UPDATE live_exchange_accounts SET adapter_environment=?,adapter_version=?,account_mode=?,
                    margin_mode=?,server_time_drift_ms=?,
                    capability_snapshot_json=?,permission_snapshot_json=?,certification_status=?,
                    certification_expires_at=?,lifecycle_state=CASE
                        WHEN ?='VST_ECONOMIC_PASSED' THEN 'LIVE_CERTIFIED'
                        WHEN ? LIKE '%BLOCKED' THEN 'ERROR' ELSE lifecycle_state END,
                    updated_at=? WHERE id=? AND telegram_id=?
            """, (report.environment, report.adapter_version, report.account_mode, report.margin_mode,
                  report.server_time_drift_ms,
                  json.dumps(report.capabilities), json.dumps(permissions, sort_keys=True), report.status,
                  report.expires_at, report.status, report.status, report.timestamp,
                  account_id, telegram_id))

    def _cache_rules(self, account_id: int, rules, expires: datetime) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""
                INSERT INTO exchange_symbol_rules_cache(account_id,exchange,environment,symbol,price_tick,
                    quantity_step,min_quantity,min_notional,max_quantity,max_leverage,adapter_version,fetched_at,expires_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(account_id,exchange,environment,symbol) DO UPDATE SET
                    price_tick=excluded.price_tick,quantity_step=excluded.quantity_step,
                    min_quantity=excluded.min_quantity,min_notional=excluded.min_notional,
                    max_quantity=excluded.max_quantity,max_leverage=excluded.max_leverage,
                    adapter_version=excluded.adapter_version,fetched_at=excluded.fetched_at,expires_at=excluded.expires_at
            """, (account_id, "bingx", self.adapter.environment, rules.symbol, str(rules.price_tick),
                  str(rules.quantity_step), str(rules.min_quantity),
                  str(rules.min_notional) if rules.min_notional is not None else None,
                  str(rules.max_quantity) if rules.max_quantity is not None else None,
                  rules.max_leverage, self.adapter.ADAPTER_VERSION, now, expires.isoformat()))

    @staticmethod
    def latest(account_id: int) -> dict | None:
        with connect() as conn:
            row = conn.execute("""
                SELECT * FROM bingx_certification_audits WHERE account_id=?
                ORDER BY started_at DESC LIMIT 1
            """, (account_id,)).fetchone()
        return dict(row) if row else None


def live_certification_valid(account_id: int, *, environment: str) -> bool:
    row = BingXCertificationService.latest(account_id)
    if not row or row["status"] != "VST_ECONOMIC_PASSED":
        return False
    if row["environment"] != environment or not row["expires_at"]:
        return False
    expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > datetime.now(timezone.utc)

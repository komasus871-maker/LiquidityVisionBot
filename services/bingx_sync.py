from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from database.database import connect
from services.exchanges.base import ExchangeError
from services.exchanges.bingx_swap import BingXSwapAdapter


logger = logging.getLogger(__name__)


class BingXSyncPersistenceError(ExchangeError):
    code = "SYNC_PERSISTENCE_ERROR"


@dataclass(frozen=True, slots=True)
class BingXSyncReport:
    success: bool
    stage: str
    error_code: str | None
    error_message: str | None
    adapter_version: str
    environment: str
    server_time_drift_ms: int | None = None
    account_mode: str | None = None
    margin_mode: str | None = None
    balance_assets: int = 0
    available_funds: str = "0"
    open_positions: int = 0
    open_orders: int = 0
    capability_count: int = 0
    symbol: str | None = None
    synchronized_at: str | None = None


class BingXAccountSyncService:
    """Stage-persistent, read-only BingX account synchronization."""

    def __init__(self, adapter: BingXSwapAdapter) -> None:
        self.adapter = adapter

    def _log(self, *, account_id: int, stage: str, status: str,
             code: str | None = None, duration_ms: float | None = None) -> None:
        logger.info("bingx_sync %s", json.dumps({
            "account_id": account_id, "adapter": self.adapter.ADAPTER_VERSION,
            "environment": self.adapter.environment, "stage": stage, "status": status,
            "code": code, "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        }, sort_keys=True))

    def _update(self, *, telegram_id: int, account_id: int, stage: str, status: str,
                error_code: str | None = None, error_message: str | None = None,
                values: dict | None = None) -> None:
        values = values or {}
        now = datetime.now(timezone.utc).isoformat()
        try:
            with connect() as conn:
                conn.execute("""
                UPDATE live_exchange_accounts SET adapter_environment=?,adapter_version=?,sync_stage=?,
                    sync_status=?,sync_error_code=?,sync_error_message=?,account_mode=COALESCE(?,account_mode),
                    margin_mode=COALESCE(?,margin_mode),server_time_drift_ms=COALESCE(?,server_time_drift_ms),
                    capability_snapshot_json=COALESCE(?,capability_snapshot_json),
                    permission_snapshot_json=COALESCE(?,permission_snapshot_json),
                    last_sync_at=CASE WHEN ?='SUCCESS' THEN ? ELSE last_sync_at END,updated_at=?
                WHERE id=? AND telegram_id=?
                """, (
                self.adapter.environment, self.adapter.ADAPTER_VERSION, stage, status,
                error_code, error_message, values.get("account_mode"), values.get("margin_mode"),
                values.get("server_time_drift_ms"), values.get("capabilities"), values.get("permissions"),
                status, now, now, account_id, telegram_id,
                ))
        except Exception as exc:
            raise BingXSyncPersistenceError("BingX synchronization state could not be persisted") from exc

    async def synchronize(self, *, telegram_id: int, account_id: int, symbol: str) -> BingXSyncReport:
        values: dict = {}
        self._update(telegram_id=telegram_id, account_id=account_id, stage="START", status="RUNNING")
        stages = (
            ("SERVER_TIME", self.adapter.server_time),
            ("ACCOUNT", self.adapter.account_info),
            ("BALANCES", self.adapter.balances),
            ("POSITIONS", self.adapter.positions),
            ("OPEN_ORDERS", lambda: self.adapter.open_orders(symbol)),
            ("SYMBOL_RULES", lambda: self.adapter.symbol_rules(symbol)),
            ("MARGIN_MODE", lambda: self.adapter.margin_mode(symbol)),
        )
        results: dict[str, object] = {}
        for stage, operation in stages:
            started = time.perf_counter()
            self._log(account_id=account_id, stage=stage, status="STARTED")
            try:
                result = await operation()
            except ExchangeError as exc:
                duration = (time.perf_counter() - started) * 1000
                self._log(account_id=account_id, stage=stage, status="FAILED", code=exc.code,
                          duration_ms=duration)
                self._update(telegram_id=telegram_id, account_id=account_id, stage=stage,
                             status="FAILED", error_code=exc.code, error_message=str(exc), values=values)
                return BingXSyncReport(False, stage, exc.code, str(exc), self.adapter.ADAPTER_VERSION,
                                       self.adapter.environment,
                                       server_time_drift_ms=values.get("server_time_drift_ms"),
                                       account_mode=values.get("account_mode"), margin_mode=values.get("margin_mode"))
            except Exception as exc:
                duration = (time.perf_counter() - started) * 1000
                code = "SYNC_INTERNAL_ERROR"
                message = f"unexpected {type(exc).__name__} during {stage.lower()}"
                self._log(account_id=account_id, stage=stage, status="FAILED", code=code,
                          duration_ms=duration)
                self._update(telegram_id=telegram_id, account_id=account_id, stage=stage,
                             status="FAILED", error_code=code, error_message=message, values=values)
                return BingXSyncReport(False, stage, code, message, self.adapter.ADAPTER_VERSION,
                                       self.adapter.environment)
            duration = (time.perf_counter() - started) * 1000
            self._log(account_id=account_id, stage=stage, status="SUCCEEDED", duration_ms=duration)
            results[stage] = result
            if stage == "SERVER_TIME":
                values["server_time_drift_ms"] = int(result) - int(time.time() * 1000)
            elif stage == "ACCOUNT":
                values["account_mode"] = result.position_mode
                values["permissions"] = json.dumps({
                    "trading": result.trading_enabled, "withdrawal": result.withdrawal_enabled,
                }, sort_keys=True)
            elif stage == "MARGIN_MODE":
                values["margin_mode"] = str(result)
            self._update(telegram_id=telegram_id, account_id=account_id, stage=stage,
                         status="RUNNING", values=values)

        capabilities = tuple(sorted(item.value for item in self.adapter.capabilities().supported))
        values["capabilities"] = json.dumps(capabilities)
        rules = results["SYMBOL_RULES"]
        expires = datetime.now(timezone.utc) + timedelta(minutes=15)
        try:
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
                  rules.max_leverage, self.adapter.ADAPTER_VERSION,
                  datetime.now(timezone.utc).isoformat(), expires.isoformat()))
        except Exception as exc:
            self._log(account_id=account_id, stage="PERSIST_SYMBOL_RULES", status="FAILED",
                      code="SYNC_PERSISTENCE_ERROR")
            self._update(telegram_id=telegram_id, account_id=account_id, stage="PERSIST_SYMBOL_RULES",
                         status="FAILED", error_code="SYNC_PERSISTENCE_ERROR",
                         error_message="BingX symbol rules could not be persisted", values=values)
            return BingXSyncReport(
                False, "PERSIST_SYMBOL_RULES", "SYNC_PERSISTENCE_ERROR",
                "BingX symbol rules could not be persisted", self.adapter.ADAPTER_VERSION,
                self.adapter.environment,
                server_time_drift_ms=values.get("server_time_drift_ms"),
                account_mode=values.get("account_mode"), margin_mode=values.get("margin_mode"),
            )
        self._update(telegram_id=telegram_id, account_id=account_id, stage="COMPLETE",
                     status="SUCCESS", values=values)
        balances = results["BALANCES"]
        positions = results["POSITIONS"]
        orders = results["OPEN_ORDERS"]
        available = sum((item.available_balance for item in balances), start=0)
        synchronized_at = datetime.now(timezone.utc).isoformat()
        self._log(account_id=account_id, stage="COMPLETE", status="SUCCESS")
        return BingXSyncReport(
            True, "COMPLETE", None, None, self.adapter.ADAPTER_VERSION, self.adapter.environment,
            server_time_drift_ms=values.get("server_time_drift_ms"),
            account_mode=values.get("account_mode"), margin_mode=values.get("margin_mode"),
            balance_assets=len(balances), available_funds=str(available), open_positions=len(positions),
            open_orders=len(orders), capability_count=len(capabilities), symbol=rules.symbol,
            synchronized_at=synchronized_at,
        )

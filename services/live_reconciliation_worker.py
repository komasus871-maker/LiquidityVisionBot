from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timezone
from html import escape

from database.database import (
    acquire_lease, connect, release_lease, runtime_finished, runtime_started,
)
from services.exchanges.credentials_store import CredentialCipher, UserExchangeCredentialStore
from services.exchanges.models import ExchangeName
from services.exchanges.registry import build_exchange_registry
from services.intelligence_alerts import IntelligenceAlertService
from services.live_reconciliation import LiveReconciliationService
from services.live_safety import LiveAuditRepository, LiveKillSwitchRepository
from services.localization import LocalizationService
from services.operator_authorization import OWNER_TELEGRAM_ID


class LiveReconciliationWorker:
    """Bounded periodic exchange-truth check for explicitly LIVE-enabled accounts."""

    worker_name = "live_reconciliation"

    def __init__(self, *, bot=None, interval_seconds: int | None = None) -> None:
        self.bot = bot
        self.interval_seconds = max(60, min(int(interval_seconds or os.getenv(
            "LIVE_RECONCILIATION_INTERVAL_SECONDS", "120")), 3600))
        self.batch_size = max(1, min(int(os.getenv("LIVE_RECONCILIATION_BATCH_SIZE", "25")), 100))
        self.enabled = os.getenv("LIVE_RECONCILIATION_ENABLED", "true").strip().lower() in {
            "1", "true", "yes", "on",
        }
        self.owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._running = True

    def _accounts(self) -> list[dict]:
        with connect() as conn:
            rows = conn.execute("""SELECT id,telegram_id,exchange FROM live_exchange_accounts
                WHERE live_enabled=1 AND lifecycle_state='LIVE_ENABLED'
                ORDER BY id LIMIT ?""", (self.batch_size,)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _adapter(telegram_id: int, exchange: str):
        name = ExchangeName(exchange)
        connection = UserExchangeCredentialStore(CredentialCipher()).get(telegram_id, name)
        if connection is None:
            raise PermissionError("LIVE_CREDENTIALS_REVOKED_OR_MISSING")
        registry = build_exchange_registry(
            credentials_override={name: connection.credentials},
            okx_passphrase=connection.passphrase if name is ExchangeName.OKX else None,
        )
        return registry.create(name)

    async def _alert(self, *, telegram_id: int, exchange: str, account_id: int,
                     alert_type: str, identity: str, details: dict) -> None:
        decision = IntelligenceAlertService().evaluate(
            telegram_id, symbol=exchange.upper(), timeframe="account", alert_type=alert_type,
            state_identity=identity, severity="CRITICAL", details=details)
        if decision["status"] != "ELIGIBLE" or self.bot is None:
            return
        try:
            i18n = LocalizationService()
            language = i18n.language(telegram_id)
            await self.bot.send_message(
                telegram_id,
                f"🛑 <b>{i18n.t('alert.live.title', language=language, exchange=i18n.market_token(exchange, language=language))}</b>\n\n"
                + i18n.t("alert.live.body", language=language,
                         account=i18n.market_token(f"#{account_id}", language=language),
                         reason=i18n.market_token(alert_type, language=language)),
                parse_mode="HTML")
        except Exception:
            IntelligenceAlertService.mark_delivery_failed(decision["alert_key"])
            logging.exception("live_reconciliation_alert_delivery_failed account_id=%s", account_id)
        else:
            IntelligenceAlertService.mark_delivered(decision["alert_key"])
        if telegram_id != OWNER_TELEGRAM_ID and alert_type in {
                "RECONCILIATION_MISMATCH", "LIVE_RISK_EVENT"}:
            owner_language = i18n.language(OWNER_TELEGRAM_ID)
            try:
                await self.bot.send_message(
                    OWNER_TELEGRAM_ID,
                    f"🛑 <b>{i18n.t('alert.live.title', language=owner_language, exchange=i18n.market_token(exchange, language=owner_language))}</b>\n\n"
                    + i18n.t("alert.live.body", language=owner_language,
                             account=i18n.market_token(f"#{account_id}", language=owner_language),
                             reason=i18n.market_token(alert_type, language=owner_language)),
                    parse_mode="HTML")
            except Exception:
                logging.exception("live_operator_alert_delivery_failed account_id=%s", account_id)

    async def _suspend_unavailable(self, account: dict, *, reason_code: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""UPDATE live_exchange_accounts SET live_enabled=0,kill_switch=1,
                execution_mode='DISABLED',lifecycle_state='SUSPENDED',updated_at=?
                WHERE id=? AND telegram_id=?""", (now, account["id"], account["telegram_id"]))
        LiveKillSwitchRepository().set(scope="CONNECTION", scope_key=str(account["id"]), active=True,
                                       reason_code=reason_code)
        LiveAuditRepository().record(
            event_type="RECONCILIATION", outcome="PROVIDER_UNAVAILABLE",
            telegram_id=int(account["telegram_id"]), account_id=int(account["id"]),
            exchange=str(account["exchange"]), metadata={"reason_code": reason_code,
            "new_entries_blocked": True, "positions_auto_closed": False})
        await self._alert(telegram_id=int(account["telegram_id"]), exchange=str(account["exchange"]),
                          account_id=int(account["id"]), alert_type="LIVE_RISK_EVENT",
                          identity=reason_code, details={"reason_code": reason_code})

    async def check_once(self) -> dict[str, int | bool]:
        if not self.enabled:
            runtime_finished(self.worker_name, processed=0, errors=0,
                             details={"state": "DISABLED_BY_CONFIGURATION"})
            return {"disabled": True, "processed": 0, "errors": 0, "mismatches": 0}
        if not acquire_lease(self.worker_name, self.owner_id, max(self.interval_seconds * 2, 180)):
            return {"skipped": True, "processed": 0, "errors": 0, "mismatches": 0}
        runtime_started(self.worker_name)
        processed = errors = mismatches = 0
        try:
            accounts = self._accounts()
            for account in accounts:
                adapter = None
                try:
                    if account["exchange"] != ExchangeName.BINGX.value:
                        raise PermissionError("LIVE_EXCHANGE_NOT_CERTIFIED")
                    adapter = self._adapter(int(account["telegram_id"]), str(account["exchange"]))
                    report = await LiveReconciliationService().reconcile(
                        adapter=adapter, telegram_id=int(account["telegram_id"]),
                        account_id=int(account["id"]), exchange=str(account["exchange"]))
                    processed += 1
                    if report["status"] == "MISMATCH":
                        mismatches += 1
                        await self._alert(
                            telegram_id=int(account["telegram_id"]), exchange=str(account["exchange"]),
                            account_id=int(account["id"]), alert_type="RECONCILIATION_MISMATCH",
                            identity="|".join(sorted(item["type"] for item in report["mismatches"])),
                            details={"mismatch_types": [item["type"] for item in report["mismatches"]]})
                except Exception as exc:
                    errors += 1
                    logging.warning("live_reconciliation_failed account_id=%s error_type=%s",
                                    account["id"], type(exc).__name__)
                    await self._suspend_unavailable(account, reason_code="RECONCILIATION_PROVIDER_UNAVAILABLE")
                finally:
                    if adapter is not None:
                        try:
                            await adapter.close()
                        except Exception:
                            logging.warning("live_reconciliation_adapter_close_failed account_id=%s",
                                            account["id"])
            runtime_finished(self.worker_name, processed=processed, errors=errors,
                             details={"accounts": len(accounts), "mismatches": mismatches})
            return {"processed": processed, "errors": errors, "mismatches": mismatches}
        finally:
            release_lease(self.worker_name, self.owner_id)

    async def run_forever(self) -> None:
        while self._running:
            try:
                await self.check_once()
            except Exception:
                logging.exception("live_reconciliation_cycle_failed")
            await asyncio.sleep(self.interval_seconds)

    def stop(self) -> None:
        self._running = False

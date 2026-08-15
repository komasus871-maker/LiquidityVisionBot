from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from database.database import connect
from services.execution_models import ExecutionMode
from services.live_safety import LiveAuditRepository, LiveKillSwitchRepository, LiveRiskRepository


class LiveAccountState(StrEnum):
    NOT_CONNECTED = "NOT_CONNECTED"
    READ_ONLY_CONNECTED = "READ_ONLY_CONNECTED"
    PREFLIGHT_READY = "PREFLIGHT_READY"
    LIVE_CERTIFIED = "LIVE_CERTIFIED"
    LIVE_ENABLED = "LIVE_ENABLED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    ERROR = "ERROR"
    KILLED = "KILLED"


ALLOWED_LIVE_ACCOUNT_TRANSITIONS = {
    LiveAccountState.NOT_CONNECTED: {LiveAccountState.READ_ONLY_CONNECTED, LiveAccountState.REVOKED,
                                     LiveAccountState.ERROR},
    LiveAccountState.READ_ONLY_CONNECTED: {LiveAccountState.PREFLIGHT_READY, LiveAccountState.REVOKED,
                                           LiveAccountState.ERROR, LiveAccountState.KILLED},
    LiveAccountState.PREFLIGHT_READY: {LiveAccountState.LIVE_CERTIFIED, LiveAccountState.READ_ONLY_CONNECTED,
                                      LiveAccountState.REVOKED, LiveAccountState.ERROR, LiveAccountState.KILLED},
    LiveAccountState.LIVE_CERTIFIED: {LiveAccountState.LIVE_ENABLED, LiveAccountState.PREFLIGHT_READY,
                                     LiveAccountState.REVOKED, LiveAccountState.ERROR, LiveAccountState.KILLED},
    LiveAccountState.LIVE_ENABLED: {LiveAccountState.SUSPENDED, LiveAccountState.REVOKED,
                                   LiveAccountState.ERROR, LiveAccountState.KILLED},
    LiveAccountState.SUSPENDED: {LiveAccountState.LIVE_CERTIFIED, LiveAccountState.REVOKED,
                                LiveAccountState.ERROR, LiveAccountState.KILLED},
    LiveAccountState.REVOKED: {LiveAccountState.READ_ONLY_CONNECTED},
    LiveAccountState.ERROR: {LiveAccountState.READ_ONLY_CONNECTED, LiveAccountState.PREFLIGHT_READY,
                             LiveAccountState.REVOKED, LiveAccountState.KILLED},
    LiveAccountState.KILLED: {LiveAccountState.READ_ONLY_CONNECTED, LiveAccountState.PREFLIGHT_READY,
                              LiveAccountState.REVOKED},
}


@dataclass(frozen=True, slots=True)
class LiveAccountConfig:
    id: int
    telegram_id: int
    exchange: str
    credential_ref: str
    execution_mode: ExecutionMode
    lifecycle_state: str
    live_enabled: bool
    dry_run_enabled: bool
    confirmed_at: str | None
    kill_switch: bool
    max_order_notional: float | None
    max_account_exposure: float | None
    max_leverage: int | None
    adapter_environment: str | None = None
    adapter_version: str | None = None
    account_mode: str | None = None
    margin_mode: str | None = None
    last_sync_at: str | None = None
    sync_status: str | None = None
    sync_stage: str | None = None
    sync_error_code: str | None = None
    sync_error_message: str | None = None
    server_time_drift_ms: int | None = None
    certification_status: str | None = None
    certification_expires_at: str | None = None


class LiveAccountRepository:
    @staticmethod
    def transition_allowed(source: str, target: str) -> bool:
        try:
            return LiveAccountState(target) in ALLOWED_LIVE_ACCOUNT_TRANSITIONS[LiveAccountState(source)]
        except (KeyError, ValueError):
            return False

    def credentials_present(self, telegram_id: int, exchange: str) -> bool:
        with connect() as conn:
            row = conn.execute("""
                SELECT 1 AS present FROM user_exchange_credentials
                WHERE telegram_id=? AND exchange=? AND status='connected'
            """, (int(telegram_id), exchange)).fetchone()
        return bool(row)

    def ensure(self, telegram_id: int, exchange: str) -> LiveAccountConfig:
        now = datetime.now(timezone.utc).isoformat()
        credential_ref = f"user_exchange_credentials:{int(telegram_id)}:{exchange}"
        with connect() as conn:
            conn.execute("""
                INSERT INTO live_exchange_accounts(telegram_id,exchange,credential_ref,created_at,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(telegram_id,exchange) DO NOTHING
            """, (int(telegram_id), exchange, credential_ref, now, now))
            conn.execute("""UPDATE live_exchange_accounts SET lifecycle_state='READ_ONLY_CONNECTED',updated_at=?
                WHERE telegram_id=? AND exchange=? AND lifecycle_state='NOT_CONNECTED'
                  AND EXISTS(SELECT 1 FROM user_exchange_credentials c
                    WHERE c.telegram_id=? AND c.exchange=? AND c.status='connected')""",
                (now, int(telegram_id), exchange, int(telegram_id), exchange))
            row = conn.execute("SELECT * FROM live_exchange_accounts WHERE telegram_id=? AND exchange=?",
                               (int(telegram_id), exchange)).fetchone()
        model = self._model(row)
        LiveRiskRepository().ensure_blocked(account_id=model.id, telegram_id=int(telegram_id))
        return model

    def get(self, telegram_id: int, exchange: str) -> LiveAccountConfig | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM live_exchange_accounts WHERE telegram_id=? AND exchange=?",
                               (int(telegram_id), exchange)).fetchone()
        return self._model(row) if row else None

    def get_by_id(self, account_id: int) -> LiveAccountConfig | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM live_exchange_accounts WHERE id=?", (int(account_id),)).fetchone()
        return self._model(row) if row else None

    def set_dry_run(self, telegram_id: int, exchange: str, enabled: bool) -> LiveAccountConfig:
        account = self.ensure(telegram_id, exchange)
        mode = ExecutionMode.LIVE_DRY_RUN.value if enabled else ExecutionMode.PAPER.value
        with connect() as conn:
            conn.execute("UPDATE live_exchange_accounts SET dry_run_enabled=?,execution_mode=?,updated_at=? WHERE id=?",
                         (int(enabled), mode, datetime.now(timezone.utc).isoformat(), account.id))
        return self.get(telegram_id, exchange)  # type: ignore[return-value]

    def begin_confirmation(self, telegram_id: int, exchange: str, *, ttl_minutes: int = 10) -> str:
        account = self.ensure(telegram_id, exchange)
        token = f"{secrets.randbelow(1_000_000):06d}"
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
        with connect() as conn:
            conn.execute("""
                UPDATE live_exchange_accounts SET confirmation_hash=?,confirmation_expires_at=?,
                    confirmed_at=NULL,live_enabled=0,kill_switch=1,updated_at=? WHERE id=?
            """, (digest, expires, datetime.now(timezone.utc).isoformat(), account.id))
        return token

    def confirm(self, telegram_id: int, exchange: str, token: str) -> bool:
        now = datetime.now(timezone.utc)
        with connect() as conn:
            row = conn.execute("SELECT * FROM live_exchange_accounts WHERE telegram_id=? AND exchange=?",
                               (int(telegram_id), exchange)).fetchone()
            if not row or not row["confirmation_hash"] or not row["confirmation_expires_at"]:
                return False
            expires = datetime.fromisoformat(str(row["confirmation_expires_at"]).replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            valid = now <= expires and hmac.compare_digest(
                str(row["confirmation_hash"]), hashlib.sha256(token.strip().encode()).hexdigest())
            if not valid:
                return False
            # Confirmation is necessary but deliberately does not enable LIVE or release the kill switch.
            conn.execute("""
                UPDATE live_exchange_accounts SET confirmed_at=?,confirmation_hash=NULL,
                    confirmation_expires_at=NULL,updated_at=? WHERE id=?
            """, (now.isoformat(), now.isoformat(), row["id"]))
        return True

    def emergency_disable(self, telegram_id: int, exchange: str) -> LiveAccountConfig:
        account = self.ensure(telegram_id, exchange)
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""
                UPDATE live_exchange_accounts SET live_enabled=0,dry_run_enabled=0,execution_mode='DISABLED',
                    lifecycle_state='KILLED',
                    kill_switch=1,confirmation_hash=NULL,confirmation_expires_at=NULL,updated_at=? WHERE id=?
            """, (now, account.id))
        LiveAuditRepository().record(event_type="LIVE_DISABLED", outcome="COMPLETE",
                                     telegram_id=telegram_id, account_id=account.id,
                                     exchange=exchange, metadata={"new_entries_blocked": True,
                                                                  "positions_auto_closed": False})
        return self.get(telegram_id, exchange)  # type: ignore[return-value]

    def suspend(self, telegram_id: int, exchange: str, *, reason_code: str) -> LiveAccountConfig:
        account = self.ensure(telegram_id, exchange)
        if not self.transition_allowed(account.lifecycle_state, LiveAccountState.SUSPENDED.value):
            raise PermissionError("LIVE_ACCOUNT_TRANSITION_INVALID")
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            cur = conn.execute("""UPDATE live_exchange_accounts SET live_enabled=0,kill_switch=1,
                execution_mode='DISABLED',lifecycle_state='SUSPENDED',updated_at=?
                WHERE id=? AND telegram_id=? AND lifecycle_state=?""",
                (now, account.id, telegram_id, account.lifecycle_state))
        if cur.rowcount != 1:
            raise PermissionError("LIVE_ACCOUNT_TRANSITION_CONFLICT")
        LiveAuditRepository().record(event_type="LIVE_SUSPENDED", outcome="COMPLETE",
                                     telegram_id=telegram_id, account_id=account.id,
                                     exchange=exchange, metadata={"reason_code": reason_code})
        return self.get(telegram_id, exchange)  # type: ignore[return-value]

    def enable_live(self, telegram_id: int, exchange: str, confirmation: str) -> LiveAccountConfig:
        """Explicit, transactional enablement; certification alone can never call this."""
        account = self.ensure(telegram_id, exchange)
        expected = f"ENABLE_LIVE_{account.id}"
        if not hmac.compare_digest(confirmation.strip(), expected):
            raise PermissionError("LIVE_ENABLE_CONFIRMATION_REQUIRED")
        if exchange != "bingx":
            raise PermissionError("LIVE_EXCHANGE_NOT_CERTIFIED")
        blockers = LiveKillSwitchRepository().blockers(
            exchange=exchange, telegram_id=telegram_id, account_id=account.id)
        if blockers:
            raise PermissionError(blockers[0].split(":", 1)[0])
        if os.getenv("BINGX_PRODUCTION_ADAPTER_ALLOWED", "false").lower() not in {"1", "true", "yes", "on"}:
            raise PermissionError("BINGX_PRODUCTION_ADAPTER_NOT_ALLOWED")
        if os.getenv("ENVIRONMENT", "").lower() not in {"production", "render"}:
            raise PermissionError("LIVE_DEPLOYMENT_ENVIRONMENT_INVALID")
        risk = LiveRiskRepository().get(account.id)
        if not risk or risk.get("status") != "ACTIVE":
            raise PermissionError("LIVE_RISK_PROFILE_REQUIRED")
        if not self.credentials_present(telegram_id, exchange) or not account.confirmed_at:
            raise PermissionError("LIVE_CREDENTIALS_OR_CONFIRMATION_MISSING")
        if account.lifecycle_state != LiveAccountState.LIVE_CERTIFIED.value:
            raise PermissionError("LIVE_ACCOUNT_NOT_CERTIFIED")
        with connect() as conn:
            certification = conn.execute("""SELECT status,expires_at FROM bingx_certification_audits
                WHERE account_id=? AND certification_type='VST_ECONOMIC'
                ORDER BY started_at DESC LIMIT 1""", (account.id,)).fetchone()
        certification = dict(certification) if certification else {}
        expires_raw = certification.get("expires_at")
        try:
            expires = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
            expires = expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            expires = datetime.min.replace(tzinfo=timezone.utc)
        if certification.get("status") != "VST_ECONOMIC_PASSED" or expires <= datetime.now(timezone.utc):
            raise PermissionError("LIVE_CERTIFICATION_REQUIRED_OR_EXPIRED")
        if account.sync_status != "SUCCESS" or account.account_mode not in {"HEDGE", "ONE_WAY"}:
            raise PermissionError("LIVE_ACCOUNT_SYNC_REQUIRED")
        if self.unresolved(telegram_id, exchange):
            raise PermissionError("LIVE_RECONCILIATION_REQUIRED")
        from services.live_copy import LiveDailyPnlService, LiveRecoveryService
        LiveDailyPnlService().require_current(account.id)
        LiveRecoveryService.require_ready(account.id)
        with connect() as conn:
            preflight = conn.execute("""SELECT ready,created_at FROM live_readiness_audits
                WHERE account_id=? ORDER BY id DESC LIMIT 1""", (account.id,)).fetchone()
        if not preflight or not bool(preflight["ready"]):
            raise PermissionError("LIVE_PREFLIGHT_REQUIRED")
        preflight_at = datetime.fromisoformat(str(preflight["created_at"]).replace("Z", "+00:00"))
        preflight_at = preflight_at if preflight_at.tzinfo else preflight_at.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - preflight_at).total_seconds() > int(
                os.getenv("LIVE_PREFLIGHT_MAX_AGE_SECONDS", "900")):
            raise PermissionError("LIVE_PREFLIGHT_STALE")
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            cur = conn.execute("""UPDATE live_exchange_accounts SET live_enabled=1,
                dry_run_enabled=0,execution_mode='LIVE',lifecycle_state='LIVE_ENABLED',kill_switch=0,updated_at=?
                WHERE id=? AND telegram_id=? AND live_enabled=0 AND confirmed_at IS NOT NULL
                  AND lifecycle_state='LIVE_CERTIFIED'""",
                (now, account.id, telegram_id))
        if cur.rowcount != 1:
            raise PermissionError("LIVE_ENABLE_STATE_CONFLICT")
        LiveAuditRepository().record(event_type="LIVE_ENABLED", outcome="COMPLETE",
                                     telegram_id=telegram_id, account_id=account.id,
                                     exchange=exchange, metadata={"explicit_confirmation": True,
                                                                  "risk_policy_version": risk.get("policy_version")})
        return self.get(telegram_id, exchange)  # type: ignore[return-value]

    @staticmethod
    def _model(row) -> LiveAccountConfig:
        row = dict(row)
        return LiveAccountConfig(
            id=int(row["id"]), telegram_id=int(row["telegram_id"]), exchange=str(row["exchange"]),
            credential_ref=str(row["credential_ref"]), execution_mode=ExecutionMode(str(row["execution_mode"])),
            lifecycle_state=str(row.get("lifecycle_state") or "NOT_CONNECTED"),
            live_enabled=bool(row["live_enabled"]), dry_run_enabled=bool(row["dry_run_enabled"]),
            confirmed_at=str(row["confirmed_at"]) if row["confirmed_at"] else None,
            kill_switch=bool(row["kill_switch"]),
            max_order_notional=float(row["max_order_notional"]) if row["max_order_notional"] is not None else None,
            max_account_exposure=float(row["max_account_exposure"]) if row["max_account_exposure"] is not None else None,
            max_leverage=int(row["max_leverage"]) if row["max_leverage"] is not None else None,
            adapter_environment=str(row.get("adapter_environment")) if row.get("adapter_environment") else None,
            adapter_version=str(row.get("adapter_version")) if row.get("adapter_version") else None,
            account_mode=str(row.get("account_mode")) if row.get("account_mode") else None,
            margin_mode=str(row.get("margin_mode")) if row.get("margin_mode") else None,
            last_sync_at=str(row.get("last_sync_at")) if row.get("last_sync_at") else None,
            sync_status=str(row.get("sync_status")) if row.get("sync_status") else None,
            sync_stage=str(row.get("sync_stage")) if row.get("sync_stage") else None,
            sync_error_code=str(row.get("sync_error_code")) if row.get("sync_error_code") else None,
            sync_error_message=str(row.get("sync_error_message")) if row.get("sync_error_message") else None,
            server_time_drift_ms=int(row["server_time_drift_ms"]) if row.get("server_time_drift_ms") is not None else None,
            certification_status=str(row.get("certification_status")) if row.get("certification_status") else None,
            certification_expires_at=str(row.get("certification_expires_at")) if row.get("certification_expires_at") else None,
        )

    def unresolved(self, telegram_id: int, exchange: str) -> tuple[dict, ...]:
        with connect() as conn:
            rows = conn.execute("""
                SELECT id,client_order_id,symbol,state,recovery_reason,updated_at FROM live_executions
                WHERE telegram_id=? AND exchange=? AND state IN ('UNKNOWN','RECOVERY_REQUIRED','RETRY_WAIT')
                ORDER BY updated_at DESC
            """, (int(telegram_id), exchange)).fetchall()
        return tuple(dict(row) for row in rows)

    def readiness_metadata(self, account_id: int) -> dict:
        with connect() as conn:
            account = conn.execute("SELECT * FROM live_exchange_accounts WHERE id=?", (account_id,)).fetchone()
            valid_rules = conn.execute("""
                SELECT COUNT(*) AS n FROM exchange_symbol_rules_cache
                WHERE account_id=? AND expires_at>?
            """, (account_id, datetime.now(timezone.utc).isoformat())).fetchone()
        result = dict(account) if account else {}
        result["valid_symbol_rules"] = int(valid_rules["n"] or 0) if valid_rules else 0
        return result

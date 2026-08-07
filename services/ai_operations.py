from __future__ import annotations

import hashlib
import asyncio
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from database.database import connect


class AIGovernanceState(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    OBSERVING = "OBSERVING"
    SHADOW_CERTIFIED = "SHADOW_CERTIFIED"
    ASSIST_CERTIFIED = "ASSIST_CERTIFIED"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


@dataclass(frozen=True, slots=True)
class AIConfigValidation:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def redacted_endpoint(endpoint: str) -> str:
    if not endpoint:
        return "unset"
    parsed = urlsplit(endpoint)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else "invalid"


def provider_identity(provider: Any) -> dict[str, Any]:
    from services.ai_trading import CONTEXT_VERSION, PROMPT_VERSION, REQUEST_FORMAT_VERSION, SCHEMA_VERSION
    identity = {
        "provider": str(getattr(provider, "name", "disabled")),
        "protocol": str(getattr(provider, "protocol", "disabled")),
        "endpoint": redacted_endpoint(str(getattr(provider, "endpoint", ""))),
        "model": str(getattr(provider, "model", "")),
        "model_version": str(getattr(provider, "model_version", "")),
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "context_version": CONTEXT_VERSION,
        "request_format_version": REQUEST_FORMAT_VERSION,
        "pricing_version": os.getenv("AI_PRICE_VERSION", "").strip() or None,
        "capabilities": asdict(getattr(provider, "capabilities")),
    }
    identity["identity_checksum"] = hashlib.sha256(_canonical(identity).encode()).hexdigest()
    return identity


class AIConfigurationValidator:
    def validate(self, provider: Any | None = None) -> AIConfigValidation:
        from services.ai_trading import AIOutputMode, build_ai_provider, resolve_output_mode
        provider = provider or build_ai_provider()
        if getattr(provider, "name", "disabled") == "disabled":
            return AIConfigValidation(True, (), ())
        errors: list[str] = []
        warnings: list[str] = []
        endpoint = str(getattr(provider, "endpoint", ""))
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            errors.append("PROVIDER_ENDPOINT_INVALID")
        if not getattr(provider, "model", ""):
            errors.append("PROVIDER_MODEL_MISSING")
        if not getattr(provider, "_api_key", ""):
            errors.append("PROVIDER_API_KEY_MISSING")
        if getattr(provider, "protocol", "") not in {"chat_completions", "responses"}:
            errors.append("PROVIDER_PROTOCOL_UNSUPPORTED")
        _, effective, reason = resolve_output_mode(provider.capabilities)
        if effective is AIOutputMode.DISABLED:
            errors.append(reason or "PROVIDER_CAPABILITY_MISMATCH")
        for key in ("AI_INPUT_COST_PER_MILLION_USD", "AI_OUTPUT_COST_PER_MILLION_USD", "AI_PRICE_VERSION"):
            if not os.getenv(key, "").strip():
                errors.append("PRICING_CONFIGURATION_MISSING")
                break
        for key in ("AI_INPUT_COST_PER_MILLION_USD", "AI_OUTPUT_COST_PER_MILLION_USD",
                    "AI_CACHED_INPUT_COST_PER_MILLION_USD"):
            raw = os.getenv(key, "").strip()
            if raw:
                try:
                    if float(raw) < 0:
                        raise ValueError
                except ValueError:
                    errors.append("PRICING_CONFIGURATION_INVALID")
                    break
        if not provider.capabilities.supports_usage_reporting:
            warnings.append("USAGE_REPORTING_UNAVAILABLE")
        return AIConfigValidation(not errors, tuple(dict.fromkeys(errors)), tuple(warnings))


class AIControlRepository:
    KILL_KEY = "GLOBAL_KILL_SWITCH"

    def kill_status(self) -> dict[str, Any]:
        with connect() as conn:
            row = conn.execute("SELECT * FROM ai_global_control WHERE control_key=?", (self.KILL_KEY,)).fetchone()
        return dict(row) if row else {"control_key": self.KILL_KEY, "enabled": 0, "reason_code": "NOT_SET", "updated_at": None}

    def set_kill(self, enabled: bool, *, actor_telegram_id: int | None, reason_code: str) -> dict[str, Any]:
        now = _now().isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO ai_global_control(control_key,enabled,reason_code,actor_telegram_id,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(control_key) DO UPDATE SET enabled=excluded.enabled,
                reason_code=excluded.reason_code,actor_telegram_id=excluded.actor_telegram_id,updated_at=excluded.updated_at""",
                (self.KILL_KEY, int(enabled), reason_code[:120], actor_telegram_id, now))
        return self.kill_status()

    def certification(self, identity_checksum: str) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("""SELECT * FROM ai_provider_certifications
                WHERE identity_checksum=? AND status='PASSED' AND expires_at>? ORDER BY id DESC LIMIT 1""",
                (identity_checksum, _now().isoformat())).fetchone()
        return dict(row) if row else None

    def governance_state(self, provider: str, identity_checksum: str) -> str:
        with connect() as conn:
            row = conn.execute("""SELECT to_state FROM ai_governance_events WHERE provider=?
                AND (identity_checksum=? OR identity_checksum IS NULL) ORDER BY id DESC LIMIT 1""",
                (provider, identity_checksum)).fetchone()
        return str(row["to_state"]) if row else AIGovernanceState.UNVERIFIED.value

    def transition(self, provider: str, identity_checksum: str, state: AIGovernanceState,
                   reason: str, actor: int | None = None, details: dict[str, Any] | None = None) -> None:
        previous = self.governance_state(provider, identity_checksum)
        with connect() as conn:
            conn.execute("""INSERT INTO ai_governance_events(provider,identity_checksum,from_state,to_state,
                reason_code,actor_telegram_id,details_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (provider, identity_checksum, previous, state.value, reason, actor,
                 _canonical(details or {}), _now().isoformat()))


class AIProviderCertificationService:
    """Certifies the configured advisory transport; never touches execution services."""

    def __init__(self, provider: Any | None = None):
        from services.ai_trading import build_ai_provider
        self.provider = provider or build_ai_provider()
        self.controls = AIControlRepository()

    async def certify(self, actor_telegram_id: int | None = None) -> dict[str, Any]:
        from services.ai_trading import (AIOutputMode, AIProviderRequest, AIResponseValidator,
            CONTEXT_VERSION, PROMPT_VERSION, REQUEST_FORMAT_VERSION, RESPONSE_SCHEMA, SCHEMA_CHECKSUM,
            SCHEMA_VERSION, SYSTEM_PROMPT, resolve_output_mode)
        identity = provider_identity(self.provider)
        validation = AIConfigurationValidator().validate(self.provider)
        checks: dict[str, Any] = {"configuration": validation.valid, "errors": validation.errors,
                                  "warnings": validation.warnings}
        status, failure = "FAILED", validation.errors[0] if validation.errors else None
        started = time.perf_counter()
        if validation.valid:
            requested, effective, reason = resolve_output_mode(self.provider.capabilities)
            request = AIProviderRequest(
                SYSTEM_PROMPT, PROMPT_VERSION,
                {"context_version": CONTEXT_VERSION, "certification_probe": True,
                 "signal_id": 0, "symbol": "CERTIFICATION", "timeframe": "none",
                 "market_timestamp": _now().isoformat(), "market": {}, "features": {},
                 "portfolio": {}, "history": {}, "deterministic": {"direction": "NEUTRAL"}},
                RESPONSE_SCHEMA, max(64, int(os.getenv("AI_MAX_TOKENS", "800"))), requested,
                effective, SCHEMA_VERSION, SCHEMA_CHECKSUM)
            try:
                response = await asyncio.wait_for(
                    self.provider.analyze(request),
                    timeout=max(1.0, float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "8"))),
                )
                checks.update({"authentication": True, "http_response": True,
                               "request_id": bool(response.provider_request_id),
                               "usage_reporting": response.input_tokens + response.output_tokens > 0,
                               "cost_parsed": response.cost_status == "PRICED",
                               "capability_compatibility": effective is not AIOutputMode.DISABLED,
                               "retryable_idempotent_requests": self.provider.capabilities.supports_retryable_idempotent_requests,
                               "downgrade": reason})
                with connect() as conn:
                    circuit = conn.execute("SELECT state FROM ai_provider_state WHERE provider=?",
                                           (identity["provider"],)).fetchone()
                checks["circuit_state"] = str(circuit["state"]) if circuit else "UNINITIALIZED"
                # Shape validation is deliberately independent of live market semantics.
                payload = response.payload
                required = set(RESPONSE_SCHEMA["required"])
                checks["schema"] = isinstance(payload, dict) and required.issubset(payload)
                if not checks["schema"]:
                    failure = "CERTIFICATION_SCHEMA_FAILED"
                elif not checks["usage_reporting"]:
                    failure = "CERTIFICATION_USAGE_MISSING"
                elif not checks["cost_parsed"]:
                    failure = "CERTIFICATION_COST_UNPRICED"
                else:
                    status, failure = "PASSED", None
            except asyncio.TimeoutError:
                failure = "CERTIFICATION_TIMEOUT"
                checks["provider_error"] = failure
            except Exception as exc:
                failure = str(getattr(exc, "code", "CERTIFICATION_PROVIDER_FAILURE"))
                checks["provider_error"] = failure
        checks["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        checks["timeout_seconds"] = float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "8"))
        now = _now()
        expires = now + timedelta(hours=max(1, int(os.getenv("AI_CERTIFICATION_TTL_HOURS", "24"))))
        certification_id = str(uuid.uuid4())
        with connect() as conn:
            conn.execute("""INSERT INTO ai_provider_certifications(certification_id,identity_checksum,provider,
                protocol,endpoint_redacted,model,model_version,prompt_version,schema_version,context_version,
                request_format_version,pricing_version,capability_snapshot_json,status,checks_json,failure_code,
                certified_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                certification_id, identity["identity_checksum"], identity["provider"], identity["protocol"],
                identity["endpoint"], identity["model"], identity["model_version"], identity["prompt_version"],
                identity["schema_version"], identity["context_version"], identity["request_format_version"],
                identity["pricing_version"], _canonical(identity["capabilities"]), status, _canonical(checks),
                failure, now.isoformat(), expires.isoformat()))
        state = AIGovernanceState.OBSERVING if status == "PASSED" else AIGovernanceState.SUSPENDED
        self.controls.transition(identity["provider"], identity["identity_checksum"], state,
                                 "CERTIFICATION_PASSED" if status == "PASSED" else failure or "CERTIFICATION_FAILED",
                                 actor_telegram_id, checks)
        return {"certification_id": certification_id, "status": status, "failure_code": failure,
                "expires_at": expires.isoformat(), "identity": identity, "checks": checks}


class AIExperimentRepository:
    def assignment(self, experiment_key: str, signal_id: int) -> str | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM ai_experiments WHERE experiment_key=? AND status='RUNNING'",
                               (experiment_key,)).fetchone()
        if not row:
            return None
        variants = json.loads(row["variants_json"])
        if not variants:
            return None
        digest = hashlib.sha256(f"{row['allocation_salt']}:{signal_id}".encode()).digest()
        return str(variants[int.from_bytes(digest[:8], "big") % len(variants)])


class AICostReconciliationRepository:
    def record(self, provider: str, period_start: str, period_end: str,
               provider_cost_usd: str | None, details: dict[str, Any] | None = None) -> dict[str, Any]:
        from decimal import Decimal
        with connect() as conn:
            row = conn.execute("""SELECT COALESCE(SUM(estimated_cost_usd),0) cost FROM ai_decisions
                WHERE provider=? AND created_at>=? AND created_at<?""", (provider, period_start, period_end)).fetchone()
            internal = Decimal(str(row["cost"] or 0))
            external = Decimal(provider_cost_usd) if provider_cost_usd is not None else None
            variance = external - internal if external is not None else None
            status = "UNRECONCILED" if external is None else "MATCHED" if abs(variance) <= Decimal("0.01") else "VARIANCE"
            now = _now().isoformat()
            conn.execute("""INSERT INTO ai_cost_reconciliations(provider,period_start,period_end,internal_cost_usd,
                provider_cost_usd,variance_usd,status,details_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (provider, period_start, period_end, str(internal), None if external is None else str(external),
                 None if variance is None else str(variance), status, _canonical(details or {}), now))
        return {"provider": provider, "internal_cost_usd": str(internal),
                "provider_cost_usd": None if external is None else str(external),
                "variance_usd": None if variance is None else str(variance), "status": status}

from __future__ import annotations

import hashlib
import asyncio
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
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


class AICertificationState(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIG_INVALID = "CONFIG_INVALID"
    MISSING = "MISSING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    IDENTITY_CHANGED = "IDENTITY_CHANGED"
    SUSPENDED = "SUSPENDED"


@dataclass(frozen=True, slots=True)
class AIConfigValidation:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _bounded_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        value = default
    if value != value or value in {float("inf"), float("-inf")}:
        value = default
    return max(low, min(high, value))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def redacted_endpoint(endpoint: str) -> str:
    if not endpoint:
        return "unset"
    parsed = urlsplit(endpoint)
    if not parsed.scheme or not parsed.hostname:
        return "invalid"
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        return "invalid"
    netloc = f"{host}:{port}" if port else host
    return f"{parsed.scheme}://{netloc}{parsed.path}"


def provider_identity(provider: Any) -> dict[str, Any]:
    from services.ai_trading import (AIProviderCapabilities, CONTEXT_VERSION, PROMPT_VERSION,
        REQUEST_FORMAT_VERSION, SCHEMA_CHECKSUM, SCHEMA_VERSION, resolve_output_mode)
    capabilities = getattr(provider, "capabilities", AIProviderCapabilities(
        supports_json_object=True, supports_json_schema=True, supports_strict_schema=True,
        supports_temperature=True, supports_max_tokens=True, supports_usage_reporting=True,
        supports_retryable_idempotent_requests=True))
    requested, effective, downgrade = resolve_output_mode(capabilities)
    protocol = str(getattr(provider, "protocol", "disabled"))
    reasoning_effort = None
    if protocol == "responses" and capabilities.supports_reasoning_models:
        reasoning_effort = os.getenv("AI_REASONING_EFFORT", "low").strip().lower()
    identity = {
        "provider": str(getattr(provider, "name", "disabled")),
        "protocol": protocol,
        "endpoint": redacted_endpoint(str(getattr(provider, "endpoint", ""))),
        "model": str(getattr(provider, "model", "")),
        "model_version": str(getattr(provider, "model_version", "")),
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "schema_checksum": SCHEMA_CHECKSUM,
        "context_version": CONTEXT_VERSION,
        "request_format_version": REQUEST_FORMAT_VERSION,
        "pricing_version": os.getenv("AI_PRICE_VERSION", "").strip() or None,
        "reasoning_effort": reasoning_effort,
        "requested_output_mode": requested.value,
        "effective_output_mode": effective.value,
        "output_downgrade_reason": downgrade,
        "capabilities": asdict(capabilities),
    }
    identity["identity_checksum"] = hashlib.sha256(_canonical(identity).encode()).hexdigest()
    return identity


class AIConfigurationValidator:
    def validate(self, provider: Any | None = None) -> AIConfigValidation:
        from services.ai_trading import (AIOutputMode, CURRENT_SCHEMA_VERSION, _env_bool,
                                         build_ai_provider, resolve_output_mode)
        provider = provider or build_ai_provider()
        if getattr(provider, "name", "disabled") == "disabled":
            return AIConfigValidation(True, (), ())
        errors: list[str] = []
        warnings: list[str] = []
        endpoint = str(getattr(provider, "endpoint", ""))
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.netloc or redacted_endpoint(endpoint) == "invalid":
            errors.append("PROVIDER_ENDPOINT_INVALID")
        if parsed.username or parsed.password:
            errors.append("PROVIDER_ENDPOINT_CREDENTIALS_FORBIDDEN")
        if not getattr(provider, "model", ""):
            errors.append("PROVIDER_MODEL_MISSING")
        if not getattr(provider, "_api_key", ""):
            errors.append("PROVIDER_API_KEY_MISSING")
        configured_schema_version = os.getenv("AI_SCHEMA_VERSION", CURRENT_SCHEMA_VERSION).strip()
        if configured_schema_version != CURRENT_SCHEMA_VERSION:
            errors.append("AI_SCHEMA_VERSION_UNSUPPORTED")
        if getattr(provider, "protocol", "") not in {"chat_completions", "responses"}:
            errors.append("PROVIDER_PROTOCOL_UNSUPPORTED")
        protocol = str(getattr(provider, "protocol", ""))
        path = parsed.path.rstrip("/").lower()
        if protocol == "responses" and path.endswith("/chat/completions"):
            errors.append("PROVIDER_ENDPOINT_PROTOCOL_MISMATCH")
        if protocol == "chat_completions" and path.endswith("/responses"):
            errors.append("PROVIDER_ENDPOINT_PROTOCOL_MISMATCH")
        if str(getattr(provider, "name", "")).lower() == "openai":
            expected = "/v1/responses" if protocol == "responses" else "/v1/chat/completions"
            if path != expected:
                errors.append("OPENAI_ENDPOINT_PATH_INVALID")
        _, effective, reason = resolve_output_mode(provider.capabilities)
        if effective is AIOutputMode.DISABLED:
            errors.append(reason or "PROVIDER_CAPABILITY_MISMATCH")
        if _env_bool("AI_REQUIRE_PRICING_FOR_REQUESTS", True):
            for key in ("AI_INPUT_COST_PER_MILLION_USD", "AI_CACHED_INPUT_COST_PER_MILLION_USD",
                        "AI_CACHE_WRITE_COST_PER_MILLION_USD",
                        "AI_OUTPUT_COST_PER_MILLION_USD", "AI_PRICE_VERSION"):
                if not os.getenv(key, "").strip():
                    errors.append("PRICING_CONFIGURATION_MISSING")
                    break
        for key in ("AI_INPUT_COST_PER_MILLION_USD", "AI_OUTPUT_COST_PER_MILLION_USD",
                    "AI_CACHED_INPUT_COST_PER_MILLION_USD",
                    "AI_CACHE_WRITE_COST_PER_MILLION_USD"):
            raw = os.getenv(key, "").strip()
            if raw:
                try:
                    value = Decimal(raw)
                    if not value.is_finite() or value < 0:
                        raise ValueError
                except (ValueError, InvalidOperation):
                    errors.append("PRICING_CONFIGURATION_INVALID")
                    break
        effort = os.getenv("AI_REASONING_EFFORT", "low").strip().lower()
        if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
            errors.append("REASONING_EFFORT_INVALID")
        if protocol == "responses":
            if not provider.capabilities.supports_max_output_tokens:
                errors.append("RESPONSES_MAX_OUTPUT_TOKENS_CAPABILITY_REQUIRED")
            if provider.capabilities.supports_max_tokens or provider.capabilities.supports_max_completion_tokens:
                errors.append("RESPONSES_CHAT_TOKEN_CAPABILITY_INVALID")
            if provider.capabilities.supports_temperature:
                errors.append("RESPONSES_TEMPERATURE_UNSUPPORTED")
        if protocol == "chat_completions":
            token_flags = (provider.capabilities.supports_max_tokens,
                           provider.capabilities.supports_max_completion_tokens)
            if sum(bool(value) for value in token_flags) != 1:
                errors.append("CHAT_TOKEN_PARAMETER_CAPABILITY_INVALID")
            if provider.capabilities.supports_max_output_tokens:
                errors.append("CHAT_RESPONSES_TOKEN_CAPABILITY_INVALID")
            if provider.capabilities.supports_reasoning_models and provider.capabilities.supports_temperature:
                errors.append("REASONING_TEMPERATURE_UNSUPPORTED")
        numeric_bounds = (
            ("AI_REQUEST_TIMEOUT_SECONDS", Decimal("1"), Decimal("120")),
            ("AI_PROVIDER_MAX_RESPONSE_BYTES", Decimal("16384"), Decimal("10000000")),
            ("AI_MAX_DAILY_REQUESTS", Decimal("1"), Decimal("100000")),
            ("AI_MAX_DAILY_REQUESTS_PER_USER", Decimal("1"), Decimal("100000")),
            ("AI_MAX_DAILY_COST_USD", Decimal("0.01"), Decimal("100000")),
            ("AI_MAX_TOKENS", Decimal("64"), Decimal("32768")),
            ("AI_MAX_CONCURRENCY", Decimal("1"), Decimal("32")),
            ("AI_OBSERVE_QUEUE_DEPTH", Decimal("1"), Decimal("1000")),
            ("AI_OBSERVE_DROP_AUDIT_LIMIT", Decimal("1"), Decimal("100")),
            ("AI_SHADOW_INTERVAL", Decimal("30"), Decimal("3600")),
            ("AI_CIRCUIT_BREAKER_FAILURES", Decimal("1"), Decimal("100")),
            ("AI_CIRCUIT_BREAKER_SECONDS", Decimal("30"), Decimal("86400")),
            ("AI_OBSERVATION_CACHE_TTL_SECONDS", Decimal("0"), Decimal("3600")),
            ("AI_PROVIDER_MAX_ATTEMPTS", Decimal("1"), Decimal("3")),
            ("AI_CERTIFICATION_MAX_TOKENS", Decimal("128"), Decimal("4096")),
            ("AI_CERTIFICATION_TTL_HOURS", Decimal("1"), Decimal("720")),
            ("AI_CERTIFICATION_REPEAT_SUPPRESSION_SECONDS", Decimal("15"), Decimal("600")),
        )
        for key, low, high in numeric_bounds:
            try:
                value = Decimal(os.getenv(key, str(low)).strip())
                if value < low or value > high:
                    raise InvalidOperation
                if key not in {"AI_REQUEST_TIMEOUT_SECONDS", "AI_MAX_DAILY_COST_USD"} and value != value.to_integral_value():
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                errors.append(f"{key}_INVALID")
        try:
            if int(os.getenv("AI_MAX_DAILY_REQUESTS_PER_USER", "10")) > int(os.getenv("AI_MAX_DAILY_REQUESTS", "100")):
                errors.append("AI_USER_DAILY_LIMIT_EXCEEDS_GLOBAL")
        except ValueError:
            pass
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

    def latest_certification(self, identity_checksum: str) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("""SELECT * FROM ai_provider_certifications
                WHERE identity_checksum=? ORDER BY id DESC LIMIT 1""",
                (identity_checksum,)).fetchone()
        return dict(row) if row else None

    def certification_state(self, provider: Any, validation: AIConfigValidation | None = None) -> dict[str, Any]:
        identity = provider_identity(provider)
        if identity["provider"] == "disabled":
            return {"state": AICertificationState.NOT_CONFIGURED.value, "row": None, "identity": identity}
        validation = validation or AIConfigurationValidator().validate(provider)
        if not validation.valid:
            return {"state": AICertificationState.CONFIG_INVALID.value, "row": None, "identity": identity}
        governance = self.governance_state(identity["provider"], identity["identity_checksum"])
        row = self.latest_certification(identity["identity_checksum"])
        if governance == AIGovernanceState.SUSPENDED.value:
            return {"state": AICertificationState.SUSPENDED.value, "row": row, "identity": identity}
        if row:
            state = str(row["status"])
            if state == AICertificationState.RUNNING.value:
                with connect() as conn:
                    claim = conn.execute("SELECT expires_at FROM ai_certification_claims WHERE identity_checksum=?",
                                         (identity["identity_checksum"],)).fetchone()
                if not claim or str(claim["expires_at"]) <= _now().isoformat():
                    completed = _now().isoformat()
                    with connect() as conn:
                        conn.execute("""UPDATE ai_provider_certifications SET status='FAILED',
                            failure_code='CERTIFICATION_INTERRUPTED',validation_stage='CERTIFICATION_CLAIM',
                            validation_code='CERTIFICATION_INTERRUPTED',completed_at=?
                            WHERE certification_id=? AND status='RUNNING'""",
                            (completed, row["certification_id"]))
                    row = self.latest_certification(identity["identity_checksum"]) or row
                    state = AICertificationState.FAILED.value
            if state == AICertificationState.PASSED.value and str(row["expires_at"]) <= _now().isoformat():
                state = AICertificationState.EXPIRED.value
            return {"state": state, "row": row, "identity": identity}
        with connect() as conn:
            changed = conn.execute("""SELECT * FROM ai_provider_certifications
                WHERE identity_checksum<>? AND status='PASSED' ORDER BY id DESC LIMIT 1""",
                (identity["identity_checksum"],)).fetchone()
        state = AICertificationState.IDENTITY_CHANGED.value if changed else AICertificationState.MISSING.value
        return {"state": state, "row": dict(changed) if changed else None, "identity": identity}

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

    @staticmethod
    def _synthetic_context() -> Any:
        from services.ai_trading import AIContext, checksum
        timestamp = _now().isoformat()
        market = {"price": 100.0, "price_unit": "synthetic_certification_unit",
                  "percentage_unit": "percent", "source": "immutable_certification_fixture"}
        features = {"certification_probe": True, "data_freshness_seconds": 0}
        return AIContext(
            telegram_id=None, signal_id=0, symbol="CERTIFICATION", timeframe="none",
            market_timestamp=timestamp, market=market, features=features,
            portfolio={"open_positions": [], "count": 0},
            history={"similar_trades": [], "sample_size": 0},
            deterministic={"status": "CERTIFICATION", "direction": "NEUTRAL",
                           "recommendation": "ABSTAIN_OR_OBSERVE"},
            market_checksum=checksum({"timestamp": timestamp, "market": market}),
            feature_checksum=checksum(features),
        )

    def _claim(self, identity_checksum: str, certification_id: str) -> bool:
        now = _now()
        try:
            timeout = max(1.0, float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "45")))
        except ValueError:
            timeout = 15.0
        suppress = _bounded_int("AI_CERTIFICATION_REPEAT_SUPPRESSION_SECONDS", 60, 15, 600)
        expires = (now + timedelta(seconds=timeout + suppress)).isoformat()
        with connect() as conn:
            cur = conn.execute("""INSERT INTO ai_certification_claims(identity_checksum,certification_id,
                claimed_at,expires_at) VALUES(?,?,?,?) ON CONFLICT(identity_checksum) DO UPDATE SET
                certification_id=excluded.certification_id,claimed_at=excluded.claimed_at,
                expires_at=excluded.expires_at WHERE ai_certification_claims.expires_at<=?""",
                (identity_checksum, certification_id, now.isoformat(), expires, now.isoformat()))
        return cur.rowcount == 1

    def _finish_claim(self, identity_checksum: str) -> None:
        suppress = _bounded_int("AI_CERTIFICATION_REPEAT_SUPPRESSION_SECONDS", 60, 15, 600)
        with connect() as conn:
            conn.execute("UPDATE ai_certification_claims SET expires_at=? WHERE identity_checksum=?",
                         ((_now() + timedelta(seconds=suppress)).isoformat(), identity_checksum))

    async def certify(self, actor_telegram_id: int | None = None) -> dict[str, Any]:
        from services.ai_trading import (AIOutputMode, AIProviderRequest, AIResponseValidator,
            PROMPT_VERSION, RESPONSE_SCHEMA, SCHEMA_CHECKSUM,
            SCHEMA_VERSION, SYSTEM_PROMPT, resolve_output_mode, validate_provider_response)
        from services.ai_context_compiler import AIContextCompiler
        identity = provider_identity(self.provider)
        validation = AIConfigurationValidator().validate(self.provider)
        certification_id = str(uuid.uuid4())
        if not self._claim(identity["identity_checksum"], certification_id):
            current = self.controls.latest_certification(identity["identity_checksum"])
            return {
                "certification_id": current["certification_id"] if current else None,
                "status": current["status"] if current else AICertificationState.RUNNING.value,
                "failure_code": "CERTIFICATION_DUPLICATE_SUPPRESSED",
                "validation_stage": "CERTIFICATION_CLAIM",
                "expires_at": current["expires_at"] if current else None,
                "identity": identity, "checks": {"duplicate_suppressed": True},
                "duplicate_suppressed": True,
            }
        started_at = _now()
        ttl = _bounded_int("AI_CERTIFICATION_TTL_HOURS", 24, 1, 720)
        expires_at = started_at + timedelta(hours=ttl)
        checks: dict[str, Any] = {
            "configuration": validation.valid, "errors": list(validation.errors),
            "warnings": list(validation.warnings), "paid_provider_request": False,
            "authentication": False, "http_response": False,
            "attempt_limit": 1, "schema_version": SCHEMA_VERSION,
            "schema_checksum": SCHEMA_CHECKSUM,
        }
        initial_status = (AICertificationState.NOT_CONFIGURED.value
                          if identity["provider"] == "disabled" else
                          AICertificationState.CONFIG_INVALID.value if not validation.valid else
                          AICertificationState.RUNNING.value)
        columns = (
            "certification_id", "identity_checksum", "provider", "protocol", "endpoint_redacted",
            "model", "model_version", "prompt_version", "schema_version", "context_version",
            "request_format_version", "pricing_version", "capability_snapshot_json", "schema_checksum",
            "reasoning_effort", "requested_output_mode", "effective_output_mode", "status", "checks_json",
            "failure_code", "validation_stage", "validation_code", "started_at", "certified_at", "expires_at",
        )
        values = (
            certification_id, identity["identity_checksum"], identity["provider"], identity["protocol"],
            identity["endpoint"], identity["model"], identity["model_version"], identity["prompt_version"],
            identity["schema_version"], identity["context_version"], identity["request_format_version"],
            identity["pricing_version"], _canonical(identity["capabilities"]), identity["schema_checksum"],
            identity["reasoning_effort"], identity["requested_output_mode"], identity["effective_output_mode"],
            initial_status, _canonical(checks), validation.errors[0] if validation.errors else None,
            "CONFIGURATION" if not validation.valid else "PROVIDER_REQUEST", validation.errors[0] if validation.errors else None,
            started_at.isoformat(), started_at.isoformat(), expires_at.isoformat(),
        )
        with connect() as conn:
            conn.execute(f"INSERT INTO ai_provider_certifications({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values)

        status = initial_status
        failure = validation.errors[0] if validation.errors else None
        validation_stage = "CONFIGURATION" if failure else "PROVIDER_REQUEST"
        response = None
        started = time.perf_counter()
        if initial_status == AICertificationState.RUNNING.value:
            requested, effective, reason = resolve_output_mode(self.provider.capabilities)
            context = self._synthetic_context()
            compiled = AIContextCompiler(_bounded_int(
                "AI_CONTEXT_MAX_CHARS", 30000, 1000, 1_000_000)).compile(context)
            checks["context_compiler"] = compiled.telemetry()
            certification_tokens = _bounded_int("AI_CERTIFICATION_MAX_TOKENS", 1200, 128, 4096)
            request = AIProviderRequest(
                SYSTEM_PROMPT, PROMPT_VERSION, compiled.payload,
                RESPONSE_SCHEMA, certification_tokens, requested,
                effective, SCHEMA_VERSION, SCHEMA_CHECKSUM)
            try:
                checks["paid_provider_request"] = True
                response = await asyncio.wait_for(
                    self.provider.analyze(request),
                    timeout=max(1.0, float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "45"))),
                )
                decision = validate_provider_response(response, context, AIResponseValidator(
                    max_age_seconds=max(60, _bounded_int("AI_CONTEXT_MAX_AGE_SECONDS", 300, 1, 86400))))
                usage_ok = response.usage_valid and response.input_tokens + response.output_tokens > 0
                request_id_ok = bool(response.provider_request_id) or not self.provider.capabilities.supports_request_id
                pricing_required = os.getenv("AI_REQUIRE_PRICING_FOR_REQUESTS", "true").strip().lower() in {"1", "true", "yes", "on"}
                pricing_ok = response.cost_status == "PRICED" or not pricing_required
                protocol_ok = response.provider_protocol == identity["protocol"]
                model_ok = bool(response.model_version)
                strict_ok = (effective is AIOutputMode.STRICT_JSON_SCHEMA and
                             response.effective_output_mode == AIOutputMode.STRICT_JSON_SCHEMA.value)
                checks.update({
                    "authentication": True, "http_response": True,
                    "protocol": protocol_ok, "model_identity": model_ok, "strict_schema": strict_ok,
                    "schema": decision.validation_stage not in {"STRUCTURED_EXTRACTION", "JSON_SCHEMA_VALIDATION"},
                    "semantic_valid": decision.valid, "validation_stage": decision.validation_stage,
                    "validation_code": decision.code, "request_id": request_id_ok,
                    "request_id_available": bool(response.provider_request_id),
                    "usage_reporting": usage_ok, "cost_parsed": pricing_ok,
                    "capability_compatibility": effective is not AIOutputMode.DISABLED,
                    "downgrade": reason, "max_output_tokens": certification_tokens,
                    "extraction_path": response.extraction_path,
                    "completion_status": response.provider_completion_status,
                    "incomplete_reason": response.provider_incomplete_reason,
                })
                if not protocol_ok:
                    failure, validation_stage = "CERTIFICATION_PROTOCOL_MISMATCH", "PROTOCOL_VALIDATION"
                elif not model_ok:
                    failure, validation_stage = "CERTIFICATION_MODEL_IDENTITY_MISSING", "MODEL_IDENTITY_VALIDATION"
                elif not strict_ok:
                    failure, validation_stage = "CERTIFICATION_STRICT_SCHEMA_REQUIRED", "OUTPUT_MODE_VALIDATION"
                elif not decision.valid:
                    failure, validation_stage = decision.code, decision.validation_stage
                elif not usage_ok:
                    failure, validation_stage = "CERTIFICATION_USAGE_MISSING", "USAGE_VALIDATION"
                elif not request_id_ok:
                    failure, validation_stage = "CERTIFICATION_REQUEST_ID_MISSING", "REQUEST_ID_VALIDATION"
                elif not pricing_ok:
                    failure, validation_stage = "CERTIFICATION_COST_UNPRICED", "PRICING_VALIDATION"
                else:
                    status, failure, validation_stage = AICertificationState.PASSED.value, None, "COMPLETE"
            except asyncio.TimeoutError:
                failure, validation_stage = "CERTIFICATION_TIMEOUT", "PROVIDER_TRANSPORT"
                checks["provider_error"] = failure
            except Exception as exc:
                failure = str(getattr(exc, "code", "CERTIFICATION_PROVIDER_FAILURE"))
                if failure in {"AI_PROVIDER_HTTP_401", "AI_PROVIDER_HTTP_403"}:
                    validation_stage = "AUTHENTICATION"
                elif failure == "PROVIDER_RESPONSE_INVALID":
                    validation_stage = "HTTP_RESPONSE_SHAPE"
                else:
                    validation_stage = "PROVIDER_TRANSPORT"
                checks["provider_error"] = failure
            if failure:
                status = AICertificationState.FAILED.value
        checks["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        try:
            checks["timeout_seconds"] = float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "45"))
        except ValueError:
            checks["timeout_seconds"] = None
        completed_at = _now()
        response_values = response or type("EmptyResponse", (), {
            "provider_request_id": None, "input_tokens": 0, "output_tokens": 0,
            "cached_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
            "estimated_cost_usd": Decimal("0"),
            "cost_status": "UNPRICED",
            "extraction_path": "none", "provider_completion_status": None,
            "provider_incomplete_reason": None,
        })()
        with connect() as conn:
            conn.execute("""UPDATE ai_provider_certifications SET status=?,checks_json=?,failure_code=?,
                validation_stage=?,validation_code=?,provider_request_id=?,returned_model_version=?,
                raw_envelope_checksum=?,extraction_path=?,provider_completion_status=?,
                provider_incomplete_reason=?,latency_ms=?,input_tokens=?,
                output_tokens=?,cached_tokens=?,cache_write_tokens=?,reasoning_tokens=?,estimated_cost_usd=?,cost_status=?,
                completed_at=?,certified_at=?,expires_at=? WHERE certification_id=?""", (
                status, _canonical(checks), failure, validation_stage, failure or "VALID",
                response_values.provider_request_id, getattr(response_values, "model_version", None),
                getattr(response_values, "raw_envelope_checksum", None), response_values.extraction_path,
                response_values.provider_completion_status, response_values.provider_incomplete_reason,
                checks["latency_ms"], response_values.input_tokens,
                response_values.output_tokens, response_values.cached_tokens,
                response_values.cache_write_tokens, response_values.reasoning_tokens,
                str(response_values.estimated_cost_usd), response_values.cost_status,
                completed_at.isoformat(), completed_at.isoformat(), expires_at.isoformat(), certification_id))
        state = AIGovernanceState.OBSERVING if status == AICertificationState.PASSED.value else AIGovernanceState.UNVERIFIED
        self.controls.transition(identity["provider"], identity["identity_checksum"], state,
                                 "CERTIFICATION_PASSED" if status == AICertificationState.PASSED.value else failure or "CERTIFICATION_FAILED",
                                 actor_telegram_id, checks)
        self._finish_claim(identity["identity_checksum"])
        return {"certification_id": certification_id, "status": status, "failure_code": failure,
                "validation_stage": validation_stage, "expires_at": expires_at.isoformat(),
                "identity": identity, "checks": checks, "duplicate_suppressed": False}


def promotion_evidence(provider: Any, target: AIGovernanceState, *, persist: bool = True) -> dict[str, Any]:
    """Evaluate promotion using only decisions from the exact certified identity."""
    from services.ai_evaluation import AIEvaluationService
    if target not in {AIGovernanceState.SHADOW_CERTIFIED, AIGovernanceState.ASSIST_CERTIFIED}:
        return {"eligible": False, "blockers": ["PROMOTION_TARGET_UNSUPPORTED"], "metrics": {}}
    identity = provider_identity(provider)
    controls = AIControlRepository()
    certification = controls.certification(identity["identity_checksum"])
    validation = AIConfigurationValidator().validate(provider)
    evaluator = AIEvaluationService()
    metrics = evaluator.metrics(identity_checksum=identity["identity_checksum"])
    minimum_key = ("AI_PROMOTION_MIN_SHADOW_DECISIONS" if target is AIGovernanceState.SHADOW_CERTIFIED
                   else "AI_PROMOTION_MIN_ASSIST_DECISIONS")
    minimum = _bounded_int(minimum_key, 30 if target is AIGovernanceState.SHADOW_CERTIFIED else 100,
                           1, 1_000_000)
    schema_min = _bounded_float("AI_PROMOTION_MIN_SCHEMA_VALID_RATE", .99, 0, 1)
    semantic_min = _bounded_float("AI_PROMOTION_MIN_SEMANTIC_VALID_RATE", .95, 0, 1)
    failure_max = _bounded_float("AI_PROMOTION_MAX_TRANSPORT_FAILURE_RATE", .05, 0, 1)
    blockers: list[str] = []
    if not validation.valid:
        blockers.append("CONFIG_INVALID")
    if not certification:
        blockers.append("VALID_CERTIFICATION_REQUIRED")
    if bool(controls.kill_status().get("enabled")):
        blockers.append("GLOBAL_AI_KILL_SWITCH")
    governance = controls.governance_state(identity["provider"], identity["identity_checksum"])
    if governance in {AIGovernanceState.SUSPENDED.value, AIGovernanceState.RETIRED.value}:
        blockers.append(f"PROVIDER_{governance}")
    if metrics["decision_count"] < minimum:
        blockers.append("CURRENT_IDENTITY_SAMPLE_MINIMUM")
    if metrics["valid_schema_rate"] < schema_min:
        blockers.append("SCHEMA_VALID_RATE_BELOW_MINIMUM")
    if metrics["semantic_valid_rate"] < semantic_min:
        blockers.append("SEMANTIC_VALID_RATE_BELOW_MINIMUM")
    if metrics["transport_failure_rate"] > failure_max:
        blockers.append("TRANSPORT_FAILURE_RATE_ABOVE_MAXIMUM")
    drift = evaluator.drift(identity_checksum=identity["identity_checksum"], minimum_samples=minimum)
    if any(item.get("severity") == "HIGH" for item in drift.get("alerts", [])):
        blockers.append("CRITICAL_PROVIDER_DRIFT")
    if os.getenv("AI_REQUIRE_PRICING_FOR_REQUESTS", "true").strip().lower() in {"1", "true", "yes", "on"}:
        if any(error.startswith("PRICING_") for error in validation.errors):
            blockers.append("PRICING_CONFIGURATION_REQUIRED")
    evidence = {
        "identity_checksum": identity["identity_checksum"], "target": target.value,
        "minimum_decisions": minimum, "decision_count": metrics["decision_count"],
        "schema_valid_count": metrics["schema_valid_count"],
        "semantic_valid_count": metrics["semantic_valid_count"],
        "schema_valid_rate": metrics["valid_schema_rate"],
        "semantic_valid_rate": metrics["semantic_valid_rate"],
        "transport_failure_count": metrics["transport_failure_count"],
        "transport_failure_rate": metrics["transport_failure_rate"],
        "timeout_count": metrics["timeout_count"], "drift_status": drift["status"],
        "certification_id": certification["certification_id"] if certification else None,
    }
    if persist:
        with connect() as conn:
            conn.execute("""INSERT INTO ai_governance_evidence(identity_checksum,target_state,decision_count,
                schema_valid_count,semantic_valid_count,transport_failure_count,timeout_count,blockers_json,
                metrics_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                identity["identity_checksum"], target.value, metrics["decision_count"],
                metrics["schema_valid_count"], metrics["semantic_valid_count"],
                metrics["transport_failure_count"], metrics["timeout_count"],
                _canonical(blockers), _canonical(evidence), _now().isoformat()))
    return {"eligible": not blockers, "blockers": blockers, "metrics": metrics,
            "evidence": evidence, "identity": identity}


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

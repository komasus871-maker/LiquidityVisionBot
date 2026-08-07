from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

import aiohttp

from database.database import connect

logger = logging.getLogger(__name__)


class AITradingMode(StrEnum):
    AI_OFF = "AI_OFF"
    AI_OBSERVE = "AI_OBSERVE"
    AI_SHADOW = "AI_SHADOW"
    AI_ASSIST = "AI_ASSIST"
    AI_GATED = "AI_GATED"


class AIAction(StrEnum):
    REJECT = "REJECT"
    WAIT = "WAIT"
    OBSERVE = "OBSERVE"
    ACCEPT_REDUCED = "ACCEPT_REDUCED"
    ACCEPT_STANDARD = "ACCEPT_STANDARD"
    ABSTAIN = "ABSTAIN"


class AIOutputMode(StrEnum):
    AUTO = "auto"
    STRICT_JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class AIProviderCapabilities:
    supports_chat_completions: bool = False
    supports_json_object: bool = False
    supports_json_schema: bool = False
    supports_strict_schema: bool = False
    supports_temperature: bool = False
    supports_max_tokens: bool = False
    supports_max_completion_tokens: bool = False
    supports_seed: bool = False
    supports_usage_reporting: bool = False
    supports_model_snapshot: bool = False
    supports_request_id: bool = False
    supports_reasoning_models: bool = False
    supports_streaming: bool = False
    supports_retryable_idempotent_requests: bool = False
    supports_max_output_tokens: bool = False


def configured_ai_mode(telegram_id: int | None = None) -> AITradingMode:
    if telegram_id is not None:
        try:
            with connect() as conn:
                row = conn.execute("SELECT mode FROM ai_user_settings WHERE telegram_id=?", (telegram_id,)).fetchone()
            if row:
                raw = str(row["mode"]).upper()
            else:
                raw = os.getenv("AI_TRADING_MODE", "AI_OFF").strip().upper()
        except Exception:
            raw = os.getenv("AI_TRADING_MODE", "AI_OFF").strip().upper()
    else:
        raw = os.getenv("AI_TRADING_MODE", "AI_OFF").strip().upper()
    try:
        mode = AITradingMode(raw)
    except ValueError:
        return AITradingMode.AI_OFF
    # Participation in admission is intentionally unavailable in v9.9.12.
    return AITradingMode.AI_OFF if mode is AITradingMode.AI_GATED else mode


def set_user_ai_mode(telegram_id: int, mode: AITradingMode) -> AITradingMode:
    effective = AITradingMode.AI_OFF if mode is AITradingMode.AI_GATED else mode
    with connect() as conn:
        conn.execute("""INSERT INTO ai_user_settings(telegram_id,mode,updated_at) VALUES(?,?,?)
            ON CONFLICT(telegram_id) DO UPDATE SET mode=excluded.mode,updated_at=excluded.updated_at""",
            (telegram_id, effective.value, datetime.now(timezone.utc).isoformat()))
    return effective


def _canonical(value: Any) -> str:
    def convert(item: Any) -> Any:
        if isinstance(item, Decimal):
            return format(item, "f")
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        raise TypeError(f"Unsupported context type: {type(item).__name__}")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False, default=convert)


def checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def transport_checksum(value: Any) -> str:
    """Hash an untrusted transport envelope without retaining or rendering its contents."""
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                            default=lambda item: f"<{type(item).__name__}>")
    return hashlib.sha256(serialized.encode()).hexdigest()


_SECRET_KEYS = re.compile(r"(api.?key|secret|token|password|database.?url|credential|passphrase)", re.I)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEYS.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = _CONTROL.sub(" ", value)[:1000]
        text = re.sub(r"(?:postgres(?:ql)?://|https?://)[^\s]+", "[REDACTED_URL]", text, flags=re.I)
        return text
    return value


@dataclass(frozen=True, slots=True)
class AIContext:
    telegram_id: int | None
    signal_id: int
    symbol: str
    timeframe: str
    market_timestamp: str
    market: dict[str, Any]
    features: dict[str, Any]
    portfolio: dict[str, Any]
    history: dict[str, Any]
    deterministic: dict[str, Any]
    market_checksum: str
    feature_checksum: str
    context_version: str = "ai-context-v1"

    def prompt_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("telegram_id", None)
        payload.pop("market_checksum", None)
        payload.pop("feature_checksum", None)
        return redact(payload)


class AIContextBuilder:
    """Builds a bounded context exclusively from persisted project truth."""

    def from_signal(self, signal_id: int, *, telegram_id: int | None = None) -> AIContext:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM signals WHERE id=? AND (? IS NULL OR owner_telegram_id=?)",
                (signal_id, telegram_id, telegram_id),
            ).fetchone()
            if row is None:
                raise KeyError(signal_id)
            item = dict(row)
            positions = conn.execute(
                """SELECT symbol,side,status,quantity,average_entry,last_price,unrealized_pnl
                   FROM paper_execution_positions WHERE telegram_id=?
                   AND status IN ('OPEN','PARTIALLY_FILLED','PARTIALLY_CLOSED') ORDER BY id""",
                (item.get("owner_telegram_id"),),
            ).fetchall() if item.get("owner_telegram_id") is not None else []
            similar = conn.execute(
                """SELECT result,realized_r,max_profit_pct,max_drawdown_pct,setup_key
                   FROM signals WHERE setup_key=? AND id<>? AND result IS NOT NULL
                   ORDER BY closed_at DESC LIMIT 20""",
                (item.get("setup_key"), signal_id),
            ).fetchall()
        try:
            features = json.loads(item.get("features_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            features = {}
        market_timestamp = str(item.get("updated_at") or item.get("created_at"))
        market = {
            "price_unit": "quote_currency_per_base_unit",
            "percentage_unit": "percent",
            "price": item.get("current_price") or item.get("entry"),
            "entry": item.get("entry"), "stop": item.get("effective_stop") or item.get("stop"),
            "take_profits": [item.get("tp1"), item.get("tp2"), item.get("tp3")],
            "expected_rr": item.get("rr"), "highest_price": item.get("highest_price"),
            "lowest_price": item.get("lowest_price"),
        }
        deterministic = {
            "status": item.get("status"), "direction": item.get("side"),
            "confidence": item.get("dynamic_confidence") or item.get("confidence"),
            "bull_score": item.get("bull_score"), "bear_score": item.get("bear_score"),
            "recommendation": item.get("recommendation"), "setup_family": item.get("setup_key"),
        }
        portfolio = {"open_positions": [dict(value) for value in positions], "count": len(positions)}
        history = {"similar_trades": [dict(value) for value in similar], "sample_size": len(similar)}
        safe_features = redact(features if isinstance(features, dict) else {})
        intelligence_keys = (
            "liquidity_sweep", "bos", "choch", "order_block", "fair_value_gap",
            "premium_discount", "liquidation_context", "funding", "open_interest",
            "volume_profile", "relative_volume", "atr", "volatility", "news_risk",
        )
        safe_features["market_intelligence"] = {
            key: safe_features.pop(key, "UNAVAILABLE") for key in intelligence_keys
        }
        return AIContext(
            telegram_id=item.get("owner_telegram_id"), signal_id=signal_id,
            symbol=str(item["symbol"]).upper(), timeframe=str(item["timeframe"]),
            market_timestamp=market_timestamp, market=market, features=safe_features,
            portfolio=portfolio, history=history, deterministic=deterministic,
            market_checksum=checksum({"timestamp": market_timestamp, "market": market}),
            feature_checksum=checksum(safe_features),
        )


@dataclass(frozen=True, slots=True)
class AIProviderRequest:
    system_prompt: str
    prompt_version: str
    context: dict[str, Any]
    response_schema: dict[str, Any]
    max_tokens: int
    requested_output_mode: AIOutputMode = AIOutputMode.AUTO
    effective_output_mode: AIOutputMode = AIOutputMode.DISABLED
    schema_version: str = "ai-decision-v1"
    schema_checksum: str = ""


@dataclass(frozen=True, slots=True)
class AIProviderResponse:
    payload: dict[str, Any] | None = field(repr=False)
    provider: str
    model: str = ""
    model_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
    raw_checksum: str | None = None
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    provider_request_id: str | None = None
    provider_protocol: str = "disabled"
    requested_output_mode: str = AIOutputMode.DISABLED.value
    effective_output_mode: str = AIOutputMode.DISABLED.value
    downgrade_reason: str | None = None
    cost_status: str = "UNPRICED"
    pricing_version: str | None = None
    provider_usage_json: str | None = None
    structured_text: str | None = field(default=None, repr=False, compare=False)
    extraction_valid: bool = True
    extraction_code: str = "VALID"
    extraction_stage: str = "STRUCTURED_EXTRACTION"
    raw_envelope_checksum: str | None = None
    identity_checksum: str | None = None
    endpoint_redacted: str | None = None
    capability_snapshot_json: str | None = None
    reasoning_effort: str | None = None
    usage_valid: bool = False
    provider_invoked: bool = False

    def __post_init__(self) -> None:
        # Directly injected test providers receive the same normalized contract as HTTP adapters.
        if isinstance(self.payload, str):
            text, payload, valid, code = normalize_structured_payload(self.payload)
            object.__setattr__(self, "payload", payload)
            object.__setattr__(self, "structured_text", text)
            object.__setattr__(self, "extraction_valid", valid)
            object.__setattr__(self, "extraction_code", code)
            if self.raw_checksum is None and text is not None:
                object.__setattr__(self, "raw_checksum", checksum(text))
        elif isinstance(self.payload, dict) and self.structured_text is None:
            text, payload, valid, code = normalize_structured_payload(self.payload)
            object.__setattr__(self, "payload", payload)
            object.__setattr__(self, "structured_text", text)
            object.__setattr__(self, "extraction_valid", valid)
            object.__setattr__(self, "extraction_code", code)
            if self.raw_checksum is None:
                object.__setattr__(self, "raw_checksum", checksum(text) if text is not None else None)


class AIProvider(Protocol):
    name: str
    model: str
    model_version: str
    protocol: str
    capabilities: AIProviderCapabilities

    async def analyze(self, request: AIProviderRequest) -> AIProviderResponse: ...
    async def health(self) -> dict[str, Any]: ...


class DisabledAIProvider:
    name = "disabled"
    model = ""
    model_version = ""
    protocol = "disabled"
    capabilities = AIProviderCapabilities()

    async def analyze(self, request: AIProviderRequest) -> AIProviderResponse:
        return AIProviderResponse(None, provider=self.name)

    async def health(self) -> dict[str, Any]:
        return {"provider": self.name, "status": "disabled"}


class MisconfiguredAIProvider(DisabledAIProvider):
    def __init__(self, protocol: str) -> None:
        self.name = os.getenv("AI_PROVIDER", "misconfigured").strip().lower()
        self.model = os.getenv("AI_MODEL", "").strip()
        self.model_version = os.getenv("AI_MODEL_VERSION", self.model).strip()
        self.protocol = protocol

    async def analyze(self, request: AIProviderRequest) -> AIProviderResponse:
        raise AIProviderError("PROVIDER_PROTOCOL_UNSUPPORTED")

    async def health(self) -> dict[str, Any]:
        return {"provider": self.name, "protocol": self.protocol, "status": "misconfigured"}


class AIProviderError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code, self.retryable = code, retryable
        super().__init__(code)


def normalize_structured_payload(value: Any) -> tuple[str | None, dict[str, Any] | None, bool, str]:
    """Parse provider structured output exactly once without retaining the transport envelope."""
    if isinstance(value, dict):
        try:
            return _canonical(value), value, True, "VALID"
        except (TypeError, ValueError):
            return None, None, False, "STRUCTURED_CANONICALIZATION_FAILED"
    if not isinstance(value, str):
        return None, None, False, "STRUCTURED_CONTENT_MISSING"
    text = value.strip()
    if not text:
        return None, None, False, "STRUCTURED_CONTENT_EMPTY"
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text, None, False, "STRUCTURED_JSON_INVALID"
    if not isinstance(parsed, dict):
        return text, None, False, "STRUCTURED_JSON_NOT_OBJECT"
    return text, parsed, True, "VALID"


def _normalized_response(*, content: Any, provider: str, model: str, model_version: str,
                         protocol: str, request: AIProviderRequest, input_tokens: int,
                         output_tokens: int, cached_tokens: int, cache_write_tokens: int,
                         reasoning_tokens: int,
                         cost: Decimal, cost_status: str, pricing_version: str | None,
                         usage_json: str, request_id: str | None, envelope: dict[str, Any],
                         usage_valid: bool) -> AIProviderResponse:
    text, payload, valid, code = normalize_structured_payload(content)
    return AIProviderResponse(
        payload=payload, provider=provider, model=model, model_version=model_version,
        input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost_usd=cost,
        raw_checksum=checksum(text) if text is not None else None,
        cached_tokens=cached_tokens, cache_write_tokens=cache_write_tokens,
        reasoning_tokens=reasoning_tokens,
        provider_request_id=request_id, provider_protocol=protocol,
        requested_output_mode=request.requested_output_mode.value,
        effective_output_mode=request.effective_output_mode.value,
        cost_status=cost_status, pricing_version=pricing_version,
        provider_usage_json=usage_json, structured_text=text,
        extraction_valid=valid, extraction_code=code,
        raw_envelope_checksum=transport_checksum(envelope),
        reasoning_effort=os.getenv("AI_REASONING_EFFORT", "low").strip().lower()
            if protocol == "responses" else None,
        usage_valid=usage_valid,
        provider_invoked=True,
    )


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int | None = None,
             maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_float(name: str, default: float, minimum: float | None = None,
               maximum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def configured_ai_interval() -> int:
    return _env_int("AI_SHADOW_INTERVAL", 60, 30, 3600)


def configured_capabilities(protocol: str) -> AIProviderCapabilities:
    chat, responses = protocol == "chat_completions", protocol == "responses"
    return AIProviderCapabilities(
        supports_chat_completions=chat,
        supports_json_object=_env_bool("AI_SUPPORTS_JSON_OBJECT", chat),
        supports_json_schema=_env_bool("AI_SUPPORTS_JSON_SCHEMA", chat or responses),
        supports_strict_schema=_env_bool("AI_SUPPORTS_STRICT_SCHEMA", chat or responses),
        supports_temperature=_env_bool("AI_SUPPORTS_TEMPERATURE", chat),
        supports_max_tokens=_env_bool("AI_SUPPORTS_MAX_TOKENS", chat),
        supports_max_completion_tokens=_env_bool("AI_SUPPORTS_MAX_COMPLETION_TOKENS", False),
        supports_seed=_env_bool("AI_SUPPORTS_SEED", False),
        supports_usage_reporting=_env_bool("AI_SUPPORTS_USAGE_REPORTING", True),
        supports_model_snapshot=_env_bool("AI_SUPPORTS_MODEL_SNAPSHOT", True),
        supports_request_id=_env_bool("AI_SUPPORTS_REQUEST_ID", True),
        supports_reasoning_models=_env_bool("AI_SUPPORTS_REASONING_MODELS", responses),
        supports_streaming=_env_bool("AI_SUPPORTS_STREAMING", False),
        supports_retryable_idempotent_requests=_env_bool("AI_SUPPORTS_RETRYABLE_IDEMPOTENT_REQUESTS", False),
        supports_max_output_tokens=_env_bool("AI_SUPPORTS_MAX_OUTPUT_TOKENS", responses),
    )


def resolve_output_mode(capabilities: AIProviderCapabilities) -> tuple[AIOutputMode, AIOutputMode, str | None]:
    raw = os.getenv("AI_STRUCTURED_OUTPUT_MODE", "json_schema").strip().lower()
    try:
        requested = AIOutputMode(raw)
    except ValueError:
        return AIOutputMode.DISABLED, AIOutputMode.DISABLED, "OUTPUT_MODE_INVALID"
    strict_required = _env_bool("AI_STRICT_SCHEMA_REQUIRED", True)
    fallback = _env_bool("AI_ALLOW_JSON_OBJECT_FALLBACK", False)
    if requested is AIOutputMode.DISABLED:
        return requested, AIOutputMode.DISABLED, None
    if requested in {AIOutputMode.AUTO, AIOutputMode.STRICT_JSON_SCHEMA}:
        if capabilities.supports_json_schema and capabilities.supports_strict_schema:
            return requested, AIOutputMode.STRICT_JSON_SCHEMA, None
        if requested is AIOutputMode.STRICT_JSON_SCHEMA and strict_required:
            return requested, AIOutputMode.DISABLED, "STRICT_SCHEMA_UNSUPPORTED"
        if fallback and capabilities.supports_json_object:
            return requested, AIOutputMode.JSON_OBJECT, "STRICT_SCHEMA_UNSUPPORTED"
        return requested, AIOutputMode.DISABLED, "PROVIDER_CAPABILITY_MISMATCH"
    if requested is AIOutputMode.JSON_OBJECT and capabilities.supports_json_object:
        return requested, AIOutputMode.JSON_OBJECT, None
    return requested, AIOutputMode.DISABLED, "PROVIDER_CAPABILITY_MISMATCH"


class BaseHTTPAIProvider:
    protocol = "unknown"

    def __init__(self) -> None:
        self.name = os.getenv("AI_PROVIDER", "disabled").strip().lower()
        self.model = os.getenv("AI_MODEL", "").strip()
        self.model_version = os.getenv("AI_MODEL_VERSION", self.model).strip()
        self.endpoint = os.getenv("AI_PROVIDER_ENDPOINT", "").strip()
        self._api_key = os.getenv("AI_PROVIDER_API_KEY", "").strip()
        self.capabilities = configured_capabilities(self.protocol)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    async def _post(self, body: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        if not self.endpoint or not self.model or not self._api_key:
            raise AIProviderError("AI_PROVIDER_NOT_CONFIGURED")
        timeout = aiohttp.ClientTimeout(total=_env_float("AI_REQUEST_TIMEOUT_SECONDS", 15, 1, 120))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.endpoint, json=body, headers=self._headers()) as response:
                    request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
                    status = response.status
                    if status >= 400:
                        retryable = status == 429 or status >= 500
                        raise AIProviderError("PROVIDER_RATE_LIMIT" if status == 429 else f"AI_PROVIDER_HTTP_{status}", retryable=retryable)
                    try:
                        maximum = _env_int("AI_PROVIDER_MAX_RESPONSE_BYTES", 1_000_000, 16_384, 10_000_000)
                        raw = bytearray()
                        async for chunk in response.content.iter_chunked(65_536):
                            raw.extend(chunk)
                            if len(raw) > maximum:
                                raise AIProviderError("PROVIDER_RESPONSE_TOO_LARGE")
                        data = json.loads(bytes(raw))
                    except AIProviderError:
                        raise
                    except Exception as exc:
                        raise AIProviderError("PROVIDER_RESPONSE_INVALID") from exc
        except asyncio.TimeoutError as exc:
            raise AIProviderError("PROVIDER_TIMEOUT", retryable=True) from exc
        except aiohttp.ClientError as exc:
            raise AIProviderError("PROVIDER_TRANSPORT_ERROR", retryable=True) from exc
        if not isinstance(data, dict):
            raise AIProviderError("PROVIDER_RESPONSE_INVALID")
        return data, request_id

    @staticmethod
    def _usage(data: dict[str, Any]) -> tuple[int, int, int, int, int, str, bool]:
        usage = data.get("usage")
        valid = isinstance(usage, dict) and any(
            key in usage for key in ("prompt_tokens", "input_tokens")
        ) and any(key in usage for key in ("completion_tokens", "output_tokens"))
        if not isinstance(usage, dict):
            usage = {}
        try:
            input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        except (TypeError, ValueError, OverflowError):
            input_tokens = output_tokens = 0
            valid = False
        input_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        output_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
        try:
            cached = int(input_details.get("cached_tokens") or 0) if isinstance(input_details, dict) else 0
            cache_write = int(input_details.get("cache_write_tokens") or 0) if isinstance(input_details, dict) else 0
            reasoning = int(output_details.get("reasoning_tokens") or 0) if isinstance(output_details, dict) else 0
        except (TypeError, ValueError, OverflowError):
            cached = cache_write = reasoning = 0
            valid = False
        # Cache reads and writes may overlap, but neither may exceed total input.
        if (min(input_tokens, output_tokens, cached, cache_write, reasoning) < 0 or
                cached > input_tokens or cache_write > input_tokens or reasoning > output_tokens):
            valid = False
        normalized = {"input_tokens": input_tokens, "output_tokens": output_tokens,
                      "cached_tokens": cached, "cache_write_tokens": cache_write,
                      "reasoning_tokens": reasoning,
                      "reported": valid}
        return input_tokens, output_tokens, cached, cache_write, reasoning, _canonical(normalized), valid

    @staticmethod
    def _cost(input_tokens: int, output_tokens: int, cached_tokens: int,
              cache_write_tokens: int,
              usage_valid: bool = True) -> tuple[Decimal, str, str | None]:
        price_version = os.getenv("AI_PRICE_VERSION", "").strip() or None
        input_raw, output_raw = os.getenv("AI_INPUT_COST_PER_MILLION_USD", "").strip(), os.getenv("AI_OUTPUT_COST_PER_MILLION_USD", "").strip()
        cached_raw = os.getenv("AI_CACHED_INPUT_COST_PER_MILLION_USD", "").strip()
        cache_write_raw = os.getenv("AI_CACHE_WRITE_COST_PER_MILLION_USD", "").strip()
        if (not usage_valid or not price_version or not input_raw or not output_raw or
                (cached_tokens and not cached_raw) or (cache_write_tokens and not cache_write_raw)):
            return Decimal("0"), "UNPRICED", price_version
        try:
            input_rate, output_rate = Decimal(input_raw), Decimal(output_raw)
            cached_rate = Decimal(cached_raw or input_raw)
            cache_write_rate = Decimal(cache_write_raw or input_raw)
        except Exception:
            return Decimal("0"), "UNPRICED", price_version
        if any(not rate.is_finite() or rate < 0 for rate in
               (input_rate, cached_rate, cache_write_rate, output_rate)):
            return Decimal("0"), "UNPRICED", price_version
        # GPT-5.6 may report overlapping cache reads and writes. Charge both reported
        # cache activities and only the input outside the larger cache span.
        ordinary = max(0, input_tokens - max(cached_tokens, cache_write_tokens))
        cost = (Decimal(ordinary) * input_rate +
                Decimal(cached_tokens) * cached_rate +
                Decimal(cache_write_tokens) * cache_write_rate +
                Decimal(output_tokens) * output_rate) / Decimal(1_000_000)
        return cost, "PRICED", price_version

    async def health(self) -> dict[str, Any]:
        configured = bool(self.endpoint and self.model and self._api_key)
        return {"provider": self.name, "protocol": self.protocol,
                "status": "configured" if configured else "misconfigured",
                "model": self.model, "model_version": self.model_version,
                "capabilities": asdict(self.capabilities)}


class ChatCompletionsAIProvider(BaseHTTPAIProvider):
    protocol = "chat_completions"

    def build_request(self, request: AIProviderRequest) -> dict[str, Any]:
        user_payload: dict[str, Any] = {"context_version": CONTEXT_VERSION, "context": request.context}
        if request.effective_output_mode is AIOutputMode.JSON_OBJECT:
            user_payload["response_schema_version"] = request.schema_version
            user_payload["response_schema"] = request.response_schema
        body: dict[str, Any] = {"model": self.model, "messages": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": _canonical(user_payload)},
        ]}
        if request.effective_output_mode is AIOutputMode.STRICT_JSON_SCHEMA:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": request.schema_version.replace("-", "_")[:64], "strict": True,
                "schema": request.response_schema,
            }}
        elif request.effective_output_mode is AIOutputMode.JSON_OBJECT:
            body["response_format"] = {"type": "json_object"}
        else:
            raise AIProviderError("PROVIDER_CAPABILITY_MISMATCH")
        if self.capabilities.supports_max_completion_tokens:
            body["max_completion_tokens"] = request.max_tokens
        elif self.capabilities.supports_max_tokens:
            body["max_tokens"] = request.max_tokens
        else:
            raise AIProviderError("MODEL_PARAMETER_UNSUPPORTED")
        if self.capabilities.supports_temperature:
            body["temperature"] = 0
        if self.capabilities.supports_seed and os.getenv("AI_SEED", "").strip():
            body["seed"] = int(os.getenv("AI_SEED", "0"))
        return body

    async def analyze(self, request: AIProviderRequest) -> AIProviderResponse:
        data, request_id = await self._post(self.build_request(request))
        choices = data.get("choices")
        extraction_error = None
        content: Any = None
        if not isinstance(choices, list) or not choices:
            extraction_error = "CHAT_CHOICES_MISSING"
        elif not isinstance(choices[0], dict):
            extraction_error = "CHAT_CHOICE_INVALID"
        elif not isinstance(choices[0].get("message"), dict):
            extraction_error = "CHAT_MESSAGE_MISSING"
        elif "content" not in choices[0]["message"]:
            extraction_error = "CHAT_CONTENT_MISSING"
        else:
            content = choices[0]["message"].get("content")
        inp, out, cached, cache_write, reasoning, usage_json, usage_valid = self._usage(data)
        cost, cost_status, price_version = self._cost(inp, out, cached, cache_write, usage_valid)
        returned_model = str(data.get("model") or "")
        response = _normalized_response(content=content, provider=self.name, model=self.model,
            model_version=returned_model, protocol=self.protocol, request=request,
            input_tokens=inp, output_tokens=out, cached_tokens=cached,
            cache_write_tokens=cache_write, reasoning_tokens=reasoning,
            cost=cost, cost_status=cost_status, pricing_version=price_version,
            usage_json=usage_json, request_id=request_id or data.get("id"), envelope=data,
            usage_valid=usage_valid)
        if extraction_error:
            response = replace(response, extraction_valid=False, extraction_code=extraction_error)
        return response


class OpenAIResponsesAIProvider(BaseHTTPAIProvider):
    protocol = "responses"

    def build_request(self, request: AIProviderRequest) -> dict[str, Any]:
        if request.effective_output_mode is not AIOutputMode.STRICT_JSON_SCHEMA:
            raise AIProviderError("PROVIDER_CAPABILITY_MISMATCH")
        if not self.capabilities.supports_max_output_tokens:
            raise AIProviderError("MODEL_PARAMETER_UNSUPPORTED")
        body = {"model": self.model, "instructions": request.system_prompt,
                "input": _canonical(request.context), "max_output_tokens": request.max_tokens,
                "text": {"format": {"type": "json_schema",
                    "name": request.schema_version.replace("-", "_")[:64],
                    "strict": True, "schema": request.response_schema}}}
        effort = os.getenv("AI_REASONING_EFFORT", "low").strip().lower()
        if self.capabilities.supports_reasoning_models:
            body["reasoning"] = {"effort": effort}
        return body

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> tuple[Any, str | None]:
        if isinstance(data.get("error"), dict):
            return None, "RESPONSES_PROVIDER_ERROR"
        if str(data.get("status") or "").lower() == "incomplete":
            return None, "RESPONSES_INCOMPLETE"
        output = data.get("output")
        if isinstance(output, list):
            # The last assistant message is the application result. Reasoning items are ignored.
            for item in reversed(output):
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                if item.get("role") not in {None, "assistant"}:
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    return None, "RESPONSES_MESSAGE_CONTENT_INVALID"
                if any(isinstance(part, dict) and part.get("type") == "refusal" for part in content):
                    return None, "PROVIDER_REFUSAL"
                for part in reversed(content):
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") in {"output_text", "text"} and "text" in part:
                        return part.get("text"), None
                return None, "RESPONSES_OUTPUT_TEXT_MISSING"
            return None, "RESPONSES_MESSAGE_MISSING"
        # Compatibility for injected providers that expose the SDK convenience field.
        if isinstance(data.get("output_text"), (str, dict)):
            return data.get("output_text"), None
        return None, "RESPONSES_OUTPUT_MISSING"

    async def analyze(self, request: AIProviderRequest) -> AIProviderResponse:
        data, request_id = await self._post(self.build_request(request))
        content, extraction_error = self._extract_content(data)
        inp, out, cached, cache_write, reasoning, usage_json, usage_valid = self._usage(data)
        cost, cost_status, price_version = self._cost(inp, out, cached, cache_write, usage_valid)
        returned_model = str(data.get("model") or "")
        response = _normalized_response(content=content, provider=self.name, model=self.model,
            model_version=returned_model, protocol=self.protocol, request=request,
            input_tokens=inp, output_tokens=out, cached_tokens=cached,
            cache_write_tokens=cache_write, reasoning_tokens=reasoning,
            cost=cost, cost_status=cost_status, pricing_version=price_version,
            usage_json=usage_json, request_id=request_id or data.get("id"), envelope=data,
            usage_valid=usage_valid)
        if extraction_error:
            response = replace(response, extraction_valid=False, extraction_code=extraction_error)
        return response


# Backward-compatible import name; v9.9.13 selects an explicit protocol below.
StructuredHTTPAIProvider = ChatCompletionsAIProvider


def build_ai_provider() -> AIProvider:
    if os.getenv("AI_PROVIDER", "disabled").strip().lower() == "disabled":
        return DisabledAIProvider()
    protocol = os.getenv("AI_PROVIDER_PROTOCOL", "responses").strip().lower()
    if protocol == "chat_completions":
        return ChatCompletionsAIProvider()
    if protocol == "responses":
        return OpenAIResponsesAIProvider()
    return MisconfiguredAIProvider(protocol)


SCHEMA_VERSION = os.getenv("AI_SCHEMA_VERSION", "ai-decision-v1").strip() or "ai-decision-v1"
CONTEXT_VERSION = "ai-context-v1"
REQUEST_FORMAT_VERSION = "ai-provider-request-v3"
_REQUIRED_FIELDS = ["regime", "direction", "confidence", "uncertainty", "recommended_action",
                    "recommended_risk_multiplier", "abstention", "supporting_factors",
                    "conflicting_factors", "invalidation_conditions", "explanation"]
RESPONSE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    # OpenAI strict structured outputs require every property to be present.
    # Semantically optional fields are therefore required but nullable.
    "required": _REQUIRED_FIELDS + ["symbol", "reference_price"],
    "properties": {
        "regime": {"type": "string", "minLength": 1, "maxLength": 80},
        "direction": {"type": "string", "enum": ["LONG", "SHORT", "NEUTRAL"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 100},
        "uncertainty": {"type": "number", "minimum": 0, "maximum": 100},
        "recommended_action": {"type": "string", "enum": [item.value for item in AIAction]},
        "recommended_risk_multiplier": {"type": "number", "minimum": 0, "maximum": 1},
        "abstention": {"type": "boolean"},
        "supporting_factors": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 20},
        "conflicting_factors": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 20},
        "invalidation_conditions": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 20},
        "explanation": {"type": "string", "minLength": 1, "maxLength": 1000},
        "symbol": {"type": ["string", "null"], "minLength": 1, "maxLength": 40},
        "reference_price": {"type": ["number", "null"], "exclusiveMinimum": 0},
    },
}
SCHEMA_CHECKSUM = checksum(RESPONSE_SCHEMA)
PROMPT_VERSION = "ai-shadow-v2-structured"
SYSTEM_PROMPT = """You are an advisory market-analysis component. Use only the supplied immutable snapshot.
Return JSON matching the supplied schema. Disclose uncertainty and contradictions. Abstain when evidence is
insufficient or stale. Never invent prices, indicators, tools, or external facts. Never issue an order command.
Distinguish setup quality from deterministic portfolio admission. Provide concise evidence, not hidden reasoning."""


@dataclass(frozen=True, slots=True)
class ValidatedDecision:
    regime: str
    direction: str
    confidence: float
    uncertainty: float
    action: AIAction
    risk_multiplier: float
    abstention: bool
    supporting: tuple[str, ...]
    conflicting: tuple[str, ...]
    invalidations: tuple[str, ...]
    explanation: str
    valid: bool
    code: str
    validation_stage: str = "DOMAIN_VALIDATION"


class AIResponseValidator:
    def __init__(self, *, min_risk: float = 0.0, max_risk: float = 1.0, max_age_seconds: int = 300):
        self.min_risk, self.max_risk = min_risk, max_risk
        self.max_age_seconds = max(1, max_age_seconds)

    def fallback(self, code: str, stage: str = "DOMAIN_VALIDATION") -> ValidatedDecision:
        return ValidatedDecision("UNKNOWN", "NEUTRAL", 0, 1, AIAction.ABSTAIN, 0, True,
                                 (), (code,), (), f"AI abstained: {code}", False, code, stage)

    @staticmethod
    def _schema_valid(value: Any, schema: dict[str, Any]) -> bool:
        expected = schema.get("type")
        accepted = expected if isinstance(expected, list) else [expected] if expected else []
        if value is None:
            return "null" in accepted
        if "object" in accepted:
            if not isinstance(value, dict):
                return False
            required = schema.get("required") or []
            if any(key not in value for key in required):
                return False
            properties = schema.get("properties") or {}
            if schema.get("additionalProperties") is False and any(key not in properties for key in value):
                return False
            return all(key not in properties or AIResponseValidator._schema_valid(item, properties[key])
                       for key, item in value.items())
        if "array" in accepted:
            if not isinstance(value, list) or len(value) > int(schema.get("maxItems", len(value))):
                return False
            return all(AIResponseValidator._schema_valid(item, schema.get("items") or {}) for item in value)
        if "string" in accepted:
            if not isinstance(value, str):
                return False
            if len(value) < int(schema.get("minLength", 0)) or len(value) > int(schema.get("maxLength", len(value))):
                return False
        elif "number" in accepted:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                return False
            if "minimum" in schema and value < schema["minimum"]:
                return False
            if "maximum" in schema and value > schema["maximum"]:
                return False
            if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
                return False
        elif "integer" in accepted:
            if isinstance(value, bool) or not isinstance(value, int):
                return False
        elif "boolean" in accepted:
            if not isinstance(value, bool):
                return False
        if "enum" in schema and value not in schema["enum"]:
            return False
        return True

    def validate(self, payload: dict[str, Any] | None, context: AIContext) -> ValidatedDecision:
        try:
            if not isinstance(payload, dict):
                return self.fallback("NORMALIZED_PAYLOAD_MISSING", "STRUCTURED_EXTRACTION")
            if not self._schema_valid(payload, RESPONSE_SCHEMA):
                return self.fallback("SCHEMA_VALIDATION_FAILED", "JSON_SCHEMA_VALIDATION")
            try:
                action = AIAction(str(payload["recommended_action"]).upper())
            except ValueError:
                return self.fallback("SCHEMA_VALIDATION_FAILED", "JSON_SCHEMA_VALIDATION")
            confidence, uncertainty = float(payload["confidence"]), float(payload["uncertainty"])
            risk = float(payload["recommended_risk_multiplier"])
            if not all(math.isfinite(x) for x in (confidence, uncertainty, risk)):
                return self.fallback("SCHEMA_VALIDATION_FAILED", "JSON_SCHEMA_VALIDATION")
            if not 0 <= confidence <= 100 or not 0 <= uncertainty <= 100:
                return self.fallback("SCHEMA_VALIDATION_FAILED", "JSON_SCHEMA_VALIDATION")
            if not self.min_risk <= risk <= self.max_risk:
                return self.fallback("RISK_MULTIPLIER_INVALID", "DOMAIN_VALIDATION")
            direction = str(payload["direction"]).upper()
            if direction not in {"LONG", "SHORT", "NEUTRAL"}:
                return self.fallback("SCHEMA_VALIDATION_FAILED", "JSON_SCHEMA_VALIDATION")
            supplied_symbol = str(payload.get("symbol") or context.symbol).upper()
            if supplied_symbol != context.symbol:
                return self.fallback("UNKNOWN_SYMBOL", "MARKET_TRUTH_VALIDATION")
            if payload.get("reference_price") is not None:
                reference = float(payload["reference_price"])
                truth = float(context.market.get("price") or 0)
                if not math.isfinite(reference) or reference <= 0 or truth <= 0 or abs(reference - truth) > max(1e-9, truth * 0.000001):
                    return self.fallback("PRICE_MISMATCH", "MARKET_TRUTH_VALIDATION")
            for key in ("supporting_factors", "conflicting_factors", "invalidation_conditions"):
                if not isinstance(payload[key], list) or not all(isinstance(item, str) for item in payload[key]):
                    return self.fallback("SCHEMA_VALIDATION_FAILED", "JSON_SCHEMA_VALIDATION")
            if not isinstance(payload["abstention"], bool):
                return self.fallback("SCHEMA_VALIDATION_FAILED", "JSON_SCHEMA_VALIDATION")
            try:
                timestamp = datetime.fromisoformat(context.market_timestamp.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return self.fallback("MARKET_TIMESTAMP_INVALID", "MARKET_TRUTH_VALIDATION")
            timestamp = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - timestamp
            if age > timedelta(seconds=self.max_age_seconds):
                return self.fallback("STALE_CONTEXT", "MARKET_TRUTH_VALIDATION")
            if age < -timedelta(seconds=_env_int("AI_CONTEXT_MAX_FUTURE_SECONDS", 5, 0, 300)):
                return self.fallback("FUTURE_CONTEXT", "MARKET_TRUTH_VALIDATION")
            factors = tuple(str(x)[:240] for x in payload["supporting_factors"] if str(x).strip())
            conflicts = tuple(str(x)[:240] for x in payload["conflicting_factors"] if str(x).strip())
            invalidations = tuple(str(x)[:240] for x in payload["invalidation_conditions"] if str(x).strip())
            if not factors and action in {AIAction.ACCEPT_REDUCED, AIAction.ACCEPT_STANDARD}:
                return self.fallback("ACTION_EVIDENCE_MISSING", "SEMANTIC_VALIDATION")
            abstention = bool(payload["abstention"])
            if action is AIAction.ABSTAIN and not abstention:
                return self.fallback("ABSTENTION_CONFLICT", "SEMANTIC_VALIDATION")
            if action in {AIAction.ACCEPT_REDUCED, AIAction.ACCEPT_STANDARD} and abstention:
                return self.fallback("ABSTENTION_CONFLICT", "SEMANTIC_VALIDATION")
            if direction == "NEUTRAL" and action is AIAction.ACCEPT_STANDARD:
                return self.fallback("DIRECTION_CONFLICT", "SEMANTIC_VALIDATION")
            expected_direction = str(context.deterministic.get("direction") or "").upper()
            if action in {AIAction.ACCEPT_REDUCED, AIAction.ACCEPT_STANDARD} and expected_direction in {"LONG", "SHORT"} and direction != expected_direction:
                return self.fallback("DIRECTION_CONFLICT", "SEMANTIC_VALIDATION")
            if action is AIAction.ACCEPT_STANDARD and risk != 1:
                return self.fallback("RISK_MULTIPLIER_INVALID", "SEMANTIC_VALIDATION")
            if action is AIAction.ACCEPT_REDUCED and not 0 < risk < 1:
                return self.fallback("RISK_MULTIPLIER_INVALID", "SEMANTIC_VALIDATION")
            if action is AIAction.REJECT and risk != 0:
                return self.fallback("RISK_MULTIPLIER_INVALID", "SEMANTIC_VALIDATION")
            if action is AIAction.ABSTAIN:
                abstention, risk = True, 0
            return ValidatedDecision(str(payload["regime"])[:80], direction, confidence, uncertainty,
                                     action, risk, abstention, factors, conflicts, invalidations,
                                     str(payload["explanation"])[:1000], True, "VALID", "COMPLETE")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return self.fallback("SCHEMA_VALIDATION_FAILED", "JSON_SCHEMA_VALIDATION")


def validate_provider_response(response: AIProviderResponse, context: AIContext,
                               validator: AIResponseValidator | None = None) -> ValidatedDecision:
    """The only extraction-to-decision validation path used by certification and analysis."""
    validator = validator or AIResponseValidator(
        max_age_seconds=_env_int("AI_CONTEXT_MAX_AGE_SECONDS", 300, 1, 86400))
    if not response.extraction_valid:
        return validator.fallback(response.extraction_code, response.extraction_stage)
    return validator.validate(response.payload, context)


class AIDecisionRepository:
    def register_prompt(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO ai_prompt_versions(prompt_version,prompt_checksum,system_prompt,
                response_schema_json,schema_version,schema_checksum,context_version,request_format_version,
                active,created_at) VALUES(?,?,?,?,?,?,?,?,1,?)
                ON CONFLICT(prompt_version) DO NOTHING""",
                (PROMPT_VERSION, checksum(SYSTEM_PROMPT), SYSTEM_PROMPT, _canonical(RESPONSE_SCHEMA),
                 SCHEMA_VERSION, SCHEMA_CHECKSUM, CONTEXT_VERSION, REQUEST_FORMAT_VERSION, now))

    def save(self, *, context: AIContext, response: AIProviderResponse,
             decision: ValidatedDecision, mode: AITradingMode, latency_ms: float,
             deterministic_accepted: bool | None = None) -> tuple[dict[str, Any], bool]:
        key = checksum({"signal": context.signal_id, "market": context.market_checksum,
                        "features": context.feature_checksum, "prompt": PROMPT_VERSION,
                        "identity": response.identity_checksum or response.provider})
        decision_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            cur = conn.execute("""INSERT INTO ai_decisions(
                decision_id,idempotency_key,correlation_id,telegram_id,signal_id,symbol,timeframe,
                market_timestamp,market_snapshot_checksum,feature_snapshot_checksum,provider,model,
                model_version,prompt_version,requested_mode,regime,direction,raw_confidence,uncertainty,
                recommended_action,recommended_risk_multiplier,abstention,supporting_factors_json,
                conflicting_factors_json,invalidation_conditions_json,explanation,schema_valid,
                validation_code,latency_ms,input_tokens,output_tokens,estimated_cost_usd,
                raw_response_checksum,deterministic_accepted,deterministic_action,
                provider_protocol,schema_version,schema_checksum,context_version,request_format_version,
                requested_output_mode,effective_output_mode,downgrade_reason,validation_stage,
                pricing_version,cost_status,cached_tokens,cache_write_tokens,reasoning_tokens,provider_request_id,
                provider_usage_json,provider_identity_checksum,provider_endpoint_redacted,
                capability_snapshot_json,reasoning_effort,extraction_stage,extraction_code,
                raw_envelope_checksum,provider_invoked,legacy_classification,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(idempotency_key) DO NOTHING""", (
                decision_id, key, f"signal:{context.signal_id}", context.telegram_id, context.signal_id,
                context.symbol, context.timeframe, context.market_timestamp, context.market_checksum,
                context.feature_checksum, response.provider, response.model, response.model_version,
                PROMPT_VERSION, mode.value, decision.regime, decision.direction, decision.confidence,
                decision.uncertainty, decision.action.value, decision.risk_multiplier, int(decision.abstention),
                _canonical(decision.supporting), _canonical(decision.conflicting),
                _canonical(decision.invalidations), decision.explanation,
                int(decision.validation_stage not in {"STRUCTURED_EXTRACTION", "JSON_SCHEMA_VALIDATION"}),
                decision.code,
                latency_ms, response.input_tokens, response.output_tokens, str(response.estimated_cost_usd),
                response.raw_checksum, None if deterministic_accepted is None else int(deterministic_accepted),
                "ACCEPT" if deterministic_accepted else "REJECT" if deterministic_accepted is False else None,
                response.provider_protocol, SCHEMA_VERSION, SCHEMA_CHECKSUM, CONTEXT_VERSION,
                REQUEST_FORMAT_VERSION, response.requested_output_mode, response.effective_output_mode,
                response.downgrade_reason, decision.validation_stage, response.pricing_version,
                response.cost_status, response.cached_tokens, response.cache_write_tokens,
                response.reasoning_tokens,
                response.provider_request_id, response.provider_usage_json,
                response.identity_checksum, response.endpoint_redacted,
                response.capability_snapshot_json, response.reasoning_effort,
                response.extraction_stage, response.extraction_code,
                response.raw_envelope_checksum, int(response.provider_invoked),
                "CURRENT_IDENTITY" if response.identity_checksum else
                "LEGACY_DISABLED" if response.provider == "disabled" else "LEGACY_UNSCOPED",
                now,
            ))
            created = cur.rowcount == 1
            row = conn.execute("SELECT * FROM ai_decisions WHERE idempotency_key=?", (key,)).fetchone()
        return dict(row), created

    def latest(self, telegram_id: int | None = None, signal_id: int | None = None) -> dict[str, Any] | None:
        clauses, params = [], []
        if telegram_id is not None:
            clauses.append("telegram_id=?")
            params.append(telegram_id)
        if signal_id is not None:
            clauses.append("signal_id=?")
            params.append(signal_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with connect() as conn:
            row = conn.execute(f"SELECT * FROM ai_decisions{where} ORDER BY id DESC LIMIT 1", params).fetchone()
        return dict(row) if row else None

    def by_idempotency(self, idempotency_key: str) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM ai_decisions WHERE idempotency_key=?",
                               (idempotency_key,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def record_observation(*, event_key: str, signal_id: int | None, telegram_id: int | None,
                           identity_checksum: str | None, snapshot_checksum: str | None,
                           status: str, reason_code: str) -> bool:
        with connect() as conn:
            cur = conn.execute("""INSERT INTO ai_observation_events(event_key,signal_id,telegram_id,
                identity_checksum,snapshot_checksum,status,reason_code,created_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(event_key) DO NOTHING""", (
                event_key, signal_id, telegram_id, identity_checksum, snapshot_checksum,
                status, reason_code, datetime.now(timezone.utc).isoformat()))
        return cur.rowcount == 1


class AITradingService:
    _semaphore: asyncio.Semaphore | None = None

    def __init__(self, provider: AIProvider | None = None, repository: AIDecisionRepository | None = None):
        self.provider = provider or build_ai_provider()
        self.repository = repository or AIDecisionRepository()
        self.context_builder = AIContextBuilder()
        self.timeout = _env_float("AI_REQUEST_TIMEOUT_SECONDS", 15, .1, 120)
        self.max_tokens = _env_int("AI_MAX_TOKENS", 600, 64, 32768)
        self.max_attempts = _env_int("AI_PROVIDER_MAX_ATTEMPTS", 1, 1, 3)
        self.max_daily_user = _env_int("AI_MAX_DAILY_REQUESTS_PER_USER", 10, 0, 100000)
        self.max_daily_global = _env_int("AI_MAX_DAILY_REQUESTS", 100, 0, 100000)
        try:
            self.max_daily_cost = max(Decimal("0"), Decimal(os.getenv("AI_MAX_DAILY_COST_USD", "5")))
        except Exception:
            self.max_daily_cost = Decimal("0")
        self.max_context_chars = _env_int("AI_CONTEXT_MAX_CHARS", 30000, 1000, 1_000_000)
        self.validator = AIResponseValidator(max_age_seconds=_env_int("AI_CONTEXT_MAX_AGE_SECONDS", 300, 1, 86400))
        self.capabilities = getattr(self.provider, "capabilities", AIProviderCapabilities(
            supports_json_object=True, supports_json_schema=True, supports_strict_schema=True,
            supports_temperature=True, supports_max_tokens=True, supports_usage_reporting=True,
            supports_retryable_idempotent_requests=True,
        ))
        if AITradingService._semaphore is None:
            AITradingService._semaphore = asyncio.Semaphore(_env_int("AI_MAX_CONCURRENCY", 1, 1, 32))

    def _identity(self) -> dict[str, Any]:
        from services.ai_operations import provider_identity
        return provider_identity(self.provider)

    def _decorate_response(self, response: AIProviderResponse,
                           identity: dict[str, Any]) -> AIProviderResponse:
        return replace(
            response,
            identity_checksum=identity["identity_checksum"],
            endpoint_redacted=identity["endpoint"],
            capability_snapshot_json=_canonical(identity["capabilities"]),
            reasoning_effort=identity.get("reasoning_effort"),
        )

    def _provider_available(self) -> bool:
        now = datetime.now(timezone.utc)
        identity = self._identity()["identity_checksum"]
        provider_key = f"{self.provider.name}:{identity}"
        with connect() as conn:
            row = conn.execute("SELECT * FROM ai_provider_state WHERE provider=? AND identity_checksum=?",
                               (provider_key, identity)).fetchone()
        if not row or row["state"] != "OPEN" or not row["opened_until"]:
            return True
        opened_until = datetime.fromisoformat(str(row["opened_until"]).replace("Z", "+00:00"))
        return opened_until <= now

    def _activation_block(self, mode: AITradingMode | None = None) -> str | None:
        from services.ai_operations import AIConfigurationValidator, AIControlRepository, provider_identity
        controls = AIControlRepository()
        if bool(controls.kill_status().get("enabled")):
            return "GLOBAL_AI_KILL_SWITCH"
        # Injected test providers are not production transports and remain independently testable.
        if not isinstance(self.provider, BaseHTTPAIProvider):
            return None
        validation = AIConfigurationValidator().validate(self.provider)
        if not validation.valid:
            return validation.errors[0]
        identity = provider_identity(self.provider)
        if controls.certification(identity["identity_checksum"]) is None:
            return "PROVIDER_NOT_CERTIFIED"
        state = controls.governance_state(identity["provider"], identity["identity_checksum"])
        if state in {"SUSPENDED", "RETIRED", "UNVERIFIED"}:
            return f"PROVIDER_{state}"
        effective_mode = mode or configured_ai_mode()
        permitted = {
            AITradingMode.AI_OBSERVE: {"OBSERVING", "SHADOW_CERTIFIED", "ASSIST_CERTIFIED"},
            AITradingMode.AI_SHADOW: {"SHADOW_CERTIFIED", "ASSIST_CERTIFIED"},
            AITradingMode.AI_ASSIST: {"ASSIST_CERTIFIED"},
        }
        if state not in permitted.get(effective_mode, set()):
            return "GOVERNANCE_MODE_NOT_CERTIFIED"
        return None

    def _provider_result(self, *, success: bool, code: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        identity_checksum = self._identity()["identity_checksum"]
        provider_key = f"{self.provider.name}:{identity_checksum}"
        with connect() as conn:
            row = conn.execute("SELECT consecutive_failures FROM ai_provider_state WHERE provider=? AND identity_checksum=?",
                               (provider_key, identity_checksum)).fetchone()
            failures = 0 if success else int(row["consecutive_failures"] or 0) + 1 if row else 1
            threshold = _env_int("AI_CIRCUIT_BREAKER_FAILURES", 5, 1, 100)
            state = "CLOSED" if success or failures < threshold else "OPEN"
            opened_until = (now + timedelta(seconds=_env_int("AI_CIRCUIT_BREAKER_SECONDS", 300, 30, 86400))).isoformat() if state == "OPEN" else None
            conn.execute("""INSERT INTO ai_provider_state(provider,state,consecutive_failures,opened_until,
                last_success_at,last_failure_at,last_error_code,identity_checksum,updated_at) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(provider) DO UPDATE SET state=excluded.state,
                consecutive_failures=excluded.consecutive_failures,opened_until=excluded.opened_until,
                last_success_at=COALESCE(excluded.last_success_at,ai_provider_state.last_success_at),
                last_failure_at=COALESCE(excluded.last_failure_at,ai_provider_state.last_failure_at),
                last_error_code=excluded.last_error_code,identity_checksum=excluded.identity_checksum,
                updated_at=excluded.updated_at""",
                (provider_key, state, failures, opened_until, now.isoformat() if success else None,
                 None if success else now.isoformat(), code, identity_checksum, now.isoformat()))

    def _usage(self, telegram_id: int | None, identity_checksum: str) -> tuple[int, int, Decimal]:
        start = datetime.now(timezone.utc).date().isoformat()
        with connect() as conn:
            global_row = conn.execute("""SELECT COUNT(*) n,COALESCE(SUM(estimated_cost_usd),0) cost
                FROM ai_decisions WHERE created_at>=? AND provider_identity_checksum=? AND provider_invoked=1""",
                (start, identity_checksum)).fetchone()
            user_row = conn.execute("""SELECT COUNT(*) n FROM ai_decisions WHERE telegram_id=?
                AND created_at>=? AND provider_identity_checksum=? AND provider_invoked=1""",
                (telegram_id, start, identity_checksum)).fetchone() if telegram_id is not None else {"n": 0}
        return int(global_row["n"]), int(user_row["n"]), Decimal(str(global_row["cost"]))

    def _claim(self, key: str) -> bool:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=max(30, int(self.timeout * self.max_attempts * 2)))).isoformat()
        with connect() as conn:
            cur = conn.execute("""INSERT INTO ai_request_claims(idempotency_key,claimed_at,expires_at)
                VALUES(?,?,?) ON CONFLICT(idempotency_key) DO UPDATE SET
                claimed_at=excluded.claimed_at,expires_at=excluded.expires_at
                WHERE ai_request_claims.expires_at<=?""", (key, now.isoformat(), expires, now.isoformat()))
        return cur.rowcount == 1

    @staticmethod
    def _release_claim(key: str) -> None:
        with connect() as conn:
            conn.execute("DELETE FROM ai_request_claims WHERE idempotency_key=?", (key,))

    async def analyze_signal(self, signal_id: int, *, telegram_id: int | None = None,
                             deterministic_accepted: bool | None = None) -> dict[str, Any] | None:
        mode = configured_ai_mode(telegram_id)
        if mode is AITradingMode.AI_OFF:
            return None
        context = self.context_builder.from_signal(signal_id, telegram_id=telegram_id)
        identity = self._identity()
        request_key = checksum({"signal": context.signal_id, "market": context.market_checksum,
                                "features": context.feature_checksum, "prompt": PROMPT_VERSION,
                                "identity": identity["identity_checksum"]})
        existing = self.repository.by_idempotency(request_key)
        if existing:
            return existing
        if not self._claim(request_key):
            self.repository.record_observation(
                event_key=f"duplicate:{request_key}", signal_id=context.signal_id,
                telegram_id=context.telegram_id, identity_checksum=identity["identity_checksum"],
                snapshot_checksum=context.market_checksum, status="SKIPPED",
                reason_code="DUPLICATE_SUPPRESSED")
            return self.repository.by_idempotency(request_key)
        total, user_total, cost = self._usage(context.telegram_id, identity["identity_checksum"])
        block = None
        if total >= self.max_daily_global or (context.telegram_id is not None and user_total >= self.max_daily_user):
            block = "DAILY_REQUEST_LIMIT"
        elif cost >= self.max_daily_cost:
            block = "COST_LIMIT"
        prompt_payload = context.prompt_payload()
        prompt_chars = len(_canonical(prompt_payload))
        if prompt_chars > self.max_context_chars:
            block = "CONTEXT_TOO_LARGE"
        block = block or self._activation_block(mode)
        input_raw = os.getenv("AI_INPUT_COST_PER_MILLION_USD", "").strip()
        output_raw = os.getenv("AI_OUTPUT_COST_PER_MILLION_USD", "").strip()
        cache_write_raw = os.getenv("AI_CACHE_WRITE_COST_PER_MILLION_USD", "").strip()
        price_version = os.getenv("AI_PRICE_VERSION", "").strip()
        priced = bool(input_raw and output_raw and price_version)
        if self.provider.name != "disabled" and not priced and _env_bool("AI_REQUIRE_PRICING_FOR_REQUESTS", True):
            block = "COST_UNPRICED"
        try:
            input_rate = max(Decimal("0"), Decimal(input_raw or "0"))
            output_rate = max(Decimal("0"), Decimal(output_raw or "0"))
            cache_write_rate = max(Decimal("0"), Decimal(cache_write_raw or input_raw or "0"))
        except Exception:
            input_rate = output_rate = cache_write_rate = Decimal("0")
            priced = False
            block = "PRICING_CONFIGURATION_INVALID"
        priced_prompt_chars = prompt_chars + len(SYSTEM_PROMPT) + len(_canonical(RESPONSE_SCHEMA))
        estimated_upper_cost = (
            Decimal(math.ceil(priced_prompt_chars / 4)) * max(input_rate, cache_write_rate) +
            Decimal(self.max_tokens) * output_rate
        ) / Decimal(1_000_000)
        if cost + estimated_upper_cost > self.max_daily_cost:
            block = "COST_LIMIT"
        self.repository.register_prompt()
        requested_output, effective_output, downgrade_reason = resolve_output_mode(self.capabilities)
        if effective_output is AIOutputMode.DISABLED and self.provider.name != "disabled":
            block = downgrade_reason or "PROVIDER_CAPABILITY_MISMATCH"
        started = time.perf_counter()
        response = self._decorate_response(AIProviderResponse(None, provider=self.provider.name,
                                      model=self.provider.model, model_version=self.provider.model_version,
                                      provider_protocol=getattr(self.provider, "protocol", "injected"),
                                      requested_output_mode=requested_output.value,
                                      effective_output_mode=effective_output.value,
                                      downgrade_reason=downgrade_reason,
                                      cost_status="PRICED" if priced else "UNPRICED",
                                      pricing_version=price_version or None), identity)
        if block or self.provider.name == "disabled" or not self._provider_available():
            decision = self.validator.fallback(block or ("PROVIDER_DISABLED" if self.provider.name == "disabled" else "CIRCUIT_OPEN"))
        else:
            request = AIProviderRequest(SYSTEM_PROMPT, PROMPT_VERSION, prompt_payload,
                                        RESPONSE_SCHEMA, self.max_tokens, requested_output,
                                        effective_output, SCHEMA_VERSION, SCHEMA_CHECKSUM)
            try:
                assert self._semaphore is not None
                last_error: Exception | None = None
                for attempt in range(self.max_attempts):
                    try:
                        response = replace(response, provider_invoked=True)
                        async with self._semaphore:
                            response = await asyncio.wait_for(self.provider.analyze(request), timeout=self.timeout)
                        last_error = None
                        break
                    except (asyncio.TimeoutError, aiohttp.ClientError, AIProviderError) as exc:
                        last_error = exc
                        retryable = self.capabilities.supports_retryable_idempotent_requests and (
                            isinstance(exc, (asyncio.TimeoutError, aiohttp.ClientError)) or (
                            isinstance(exc, AIProviderError) and exc.retryable)
                        )
                        if retryable and attempt + 1 < self.max_attempts:
                            await asyncio.sleep(min(0.5, 0.1 * (2 ** attempt)))
                        else:
                            break
                if last_error is not None:
                    raise last_error
                response = self._decorate_response(replace(
                    response, downgrade_reason=downgrade_reason or response.downgrade_reason,
                    provider_invoked=True), identity)
                decision = validate_provider_response(response, context, self.validator)
                self._provider_result(success=decision.valid, code=None if decision.valid else decision.code)
            except (asyncio.TimeoutError, AIProviderError) as exc:
                code = exc.code if isinstance(exc, AIProviderError) else "PROVIDER_TIMEOUT"
                stage = "HTTP_RESPONSE_SHAPE" if code == "PROVIDER_RESPONSE_INVALID" else "PROVIDER_TRANSPORT"
                decision = self.validator.fallback(code, stage)
                self._provider_result(success=False, code=decision.code)
            except Exception:
                decision = self.validator.fallback("PROVIDER_FAILURE", "PROVIDER_TRANSPORT")
                self._provider_result(success=False, code=decision.code)
        latency = (time.perf_counter() - started) * 1000
        row, _ = self.repository.save(context=context, response=response, decision=decision,
                                      mode=mode, latency_ms=latency,
                                      deterministic_accepted=deterministic_accepted)
        self.repository.record_observation(
            event_key=f"decision:{row['decision_id']}", signal_id=context.signal_id,
            telegram_id=context.telegram_id, identity_checksum=identity["identity_checksum"],
            snapshot_checksum=context.market_checksum,
            status="COMPLETED" if decision.valid else "SKIPPED", reason_code=decision.code)
        self._release_claim(request_key)
        return row


class AIShadowWorker:
    def __init__(self, interval_seconds: int = 60):
        self.interval_seconds = max(30, interval_seconds)
        self._stop = asyncio.Event()
        self.service = AITradingService()

    def stop(self) -> None:
        self._stop.set()

    async def check_once(self) -> dict[str, int]:
        from services.ai_evaluation import AIOutcomeRepository
        outcomes = AIOutcomeRepository().attach_closed_signals(limit=100)
        if configured_ai_mode() is AITradingMode.AI_OFF:
            return {"processed": 0, "failed": 0, "outcomes_attached": outcomes}
        queue_depth = _env_int("AI_OBSERVE_QUEUE_DEPTH", 10, 1, 100)
        drop_audit_limit = _env_int("AI_OBSERVE_DROP_AUDIT_LIMIT", 25, 1, 100)
        identity = self.service._identity()
        with connect() as conn:
            rows = conn.execute("""SELECT s.id,s.owner_telegram_id,s.updated_at FROM signals s
                WHERE s.status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')
                AND NOT EXISTS(SELECT 1 FROM ai_decisions d WHERE d.signal_id=s.id
                    AND d.created_at>=s.updated_at AND d.provider_identity_checksum=?)
                ORDER BY s.id LIMIT ?""",
                (identity["identity_checksum"], queue_depth + drop_audit_limit)).fetchall()
        queued, overflow = rows[:queue_depth], rows[queue_depth:]
        for raw in overflow:
            overflow_snapshot = checksum({"signal": raw["id"], "updated_at": raw["updated_at"]})
            self.service.repository.record_observation(
                event_key=f"queue-full:{identity['identity_checksum']}:{overflow_snapshot}",
                signal_id=int(raw["id"]), telegram_id=raw["owner_telegram_id"],
                identity_checksum=identity["identity_checksum"], snapshot_checksum=overflow_snapshot,
                status="SKIPPED", reason_code="OBSERVATION_QUEUE_FULL")
        # Snapshot-level duplicate suppression is enforced again by the ledger.
        processed = failed = 0
        for raw in queued:
            try:
                await self.service.analyze_signal(int(raw["id"]), telegram_id=raw["owner_telegram_id"])
                processed += 1
            except Exception:
                failed += 1
                logger.exception("AI shadow decision failed for signal_id=%s", raw["id"])
        return {"processed": processed, "failed": failed, "dropped": len(overflow),
                "outcomes_attached": outcomes}

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self.check_once()
            except Exception:
                logger.exception("AI shadow worker cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

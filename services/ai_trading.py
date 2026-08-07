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
from dataclasses import asdict, dataclass, replace
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


def configured_ai_mode(telegram_id: int | None = None) -> AITradingMode:
    if telegram_id is not None:
        try:
            with connect() as conn:
                row = conn.execute("SELECT mode FROM ai_user_settings WHERE telegram_id=?", (telegram_id,)).fetchone()
            if row:
                raw = str(row["mode"]).upper()
            else:
                raw = os.getenv("AI_TRADING_MODE", "AI_SHADOW").strip().upper()
        except Exception:
            raw = os.getenv("AI_TRADING_MODE", "AI_SHADOW").strip().upper()
    else:
        raw = os.getenv("AI_TRADING_MODE", "AI_SHADOW").strip().upper()
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
        try:
            observed = datetime.fromisoformat(market_timestamp.replace("Z", "+00:00"))
            safe_features["data_freshness_seconds"] = max(0, int((datetime.now(timezone.utc) - observed).total_seconds()))
        except (TypeError, ValueError):
            safe_features["data_freshness_seconds"] = "UNAVAILABLE"
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
    payload: dict[str, Any] | str | None
    provider: str
    model: str = ""
    model_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
    raw_checksum: str | None = None
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    provider_request_id: str | None = None
    provider_protocol: str = "disabled"
    requested_output_mode: str = AIOutputMode.DISABLED.value
    effective_output_mode: str = AIOutputMode.DISABLED.value
    downgrade_reason: str | None = None
    cost_status: str = "UNPRICED"
    pricing_version: str | None = None
    provider_usage_json: str | None = None


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


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() in {"1", "true", "yes", "on"}


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
        supports_retryable_idempotent_requests=_env_bool("AI_SUPPORTS_RETRYABLE_IDEMPOTENT_REQUESTS", True),
    )


def resolve_output_mode(capabilities: AIProviderCapabilities) -> tuple[AIOutputMode, AIOutputMode, str | None]:
    raw = os.getenv("AI_STRUCTURED_OUTPUT_MODE", "auto").strip().lower()
    try:
        requested = AIOutputMode(raw)
    except ValueError:
        return AIOutputMode.DISABLED, AIOutputMode.DISABLED, "OUTPUT_MODE_INVALID"
    strict_required = _env_bool("AI_STRICT_SCHEMA_REQUIRED", False)
    fallback = _env_bool("AI_ALLOW_JSON_OBJECT_FALLBACK", True)
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
        timeout = aiohttp.ClientTimeout(total=max(1.0, float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "8"))))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.endpoint, json=body, headers=self._headers()) as response:
                    request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
                    status = response.status
                    if status >= 400:
                        retryable = status == 429 or status >= 500
                        raise AIProviderError("PROVIDER_RATE_LIMIT" if status == 429 else f"AI_PROVIDER_HTTP_{status}", retryable=retryable)
                    try:
                        data = await response.json()
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
    def _usage(data: dict[str, Any]) -> tuple[int, int, int, int, str]:
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        input_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        output_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
        cached = int(input_details.get("cached_tokens") or 0) if isinstance(input_details, dict) else 0
        reasoning = int(output_details.get("reasoning_tokens") or 0) if isinstance(output_details, dict) else 0
        return input_tokens, output_tokens, cached, reasoning, _canonical(usage)

    @staticmethod
    def _cost(input_tokens: int, output_tokens: int, cached_tokens: int) -> tuple[Decimal, str, str | None]:
        price_version = os.getenv("AI_PRICE_VERSION", "").strip() or None
        input_raw, output_raw = os.getenv("AI_INPUT_COST_PER_MILLION_USD", "").strip(), os.getenv("AI_OUTPUT_COST_PER_MILLION_USD", "").strip()
        if not price_version or not input_raw or not output_raw:
            return Decimal("0"), "UNPRICED", price_version
        input_rate, output_rate = max(Decimal("0"), Decimal(input_raw)), max(Decimal("0"), Decimal(output_raw))
        cached_rate = max(Decimal("0"), Decimal(os.getenv("AI_CACHED_INPUT_COST_PER_MILLION_USD", input_raw)))
        uncached = max(0, input_tokens - cached_tokens)
        cost = (Decimal(uncached) * input_rate + Decimal(cached_tokens) * cached_rate + Decimal(output_tokens) * output_rate) / Decimal(1_000_000)
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
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("PROVIDER_RESPONSE_INVALID") from exc
        if not isinstance(content, (str, dict)):
            raise AIProviderError("PROVIDER_RESPONSE_INVALID")
        inp, out, cached, reasoning, usage_json = self._usage(data)
        cost, cost_status, price_version = self._cost(inp, out, cached)
        returned_model = str(data.get("model") or self.model_version)
        return AIProviderResponse(content, self.name, self.model, returned_model, inp, out, cost,
            checksum(content), cached, reasoning, request_id or data.get("id"), self.protocol,
            request.requested_output_mode.value, request.effective_output_mode.value, None,
            cost_status, price_version, usage_json)


class OpenAIResponsesAIProvider(BaseHTTPAIProvider):
    protocol = "responses"

    def build_request(self, request: AIProviderRequest) -> dict[str, Any]:
        if request.effective_output_mode is not AIOutputMode.STRICT_JSON_SCHEMA:
            raise AIProviderError("PROVIDER_CAPABILITY_MISMATCH")
        return {"model": self.model, "instructions": request.system_prompt,
                "input": _canonical(request.context), "max_output_tokens": request.max_tokens,
                "text": {"format": {"type": "json_schema",
                    "name": request.schema_version.replace("-", "_")[:64],
                    "strict": True, "schema": request.response_schema}}}

    async def analyze(self, request: AIProviderRequest) -> AIProviderResponse:
        data, request_id = await self._post(self.build_request(request))
        content = data.get("output_text")
        if not isinstance(content, str):
            try:
                content = data["output"][0]["content"][0]["text"]
            except (KeyError, IndexError, TypeError) as exc:
                raise AIProviderError("PROVIDER_RESPONSE_INVALID") from exc
        inp, out, cached, reasoning, usage_json = self._usage(data)
        cost, cost_status, price_version = self._cost(inp, out, cached)
        returned_model = str(data.get("model") or self.model_version)
        return AIProviderResponse(content, self.name, self.model, returned_model, inp, out, cost,
            checksum(content), cached, reasoning, request_id or data.get("id"), self.protocol,
            request.requested_output_mode.value, request.effective_output_mode.value, None,
            cost_status, price_version, usage_json)


# Backward-compatible import name; v9.9.13 selects an explicit protocol below.
StructuredHTTPAIProvider = ChatCompletionsAIProvider


def build_ai_provider() -> AIProvider:
    if os.getenv("AI_PROVIDER", "disabled").strip().lower() == "disabled":
        return DisabledAIProvider()
    protocol = os.getenv("AI_PROVIDER_PROTOCOL", "chat_completions").strip().lower()
    if protocol == "chat_completions":
        return ChatCompletionsAIProvider()
    if protocol == "responses":
        return OpenAIResponsesAIProvider()
    return MisconfiguredAIProvider(protocol)


SCHEMA_VERSION = os.getenv("AI_SCHEMA_VERSION", "ai-decision-v1").strip() or "ai-decision-v1"
CONTEXT_VERSION = "ai-context-v1"
REQUEST_FORMAT_VERSION = "ai-provider-request-v2"
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

    def validate(self, raw: dict[str, Any] | str | None, context: AIContext) -> ValidatedDecision:
        try:
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, json.JSONDecodeError):
                return self.fallback("OUTPUT_NOT_JSON", "JSON_PARSING")
            if not isinstance(payload, dict):
                return self.fallback("OUTPUT_NOT_JSON", "JSON_PARSING")
            missing = [key for key in _REQUIRED_FIELDS if key not in payload]
            if missing:
                return self.fallback("SCHEMA_VALIDATION_FAILED", "JSON_SCHEMA_VALIDATION")
            allowed = set(RESPONSE_SCHEMA["properties"])
            if set(payload) - allowed:
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
            timestamp = datetime.fromisoformat(context.market_timestamp.replace("Z", "+00:00"))
            timestamp = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - timestamp > timedelta(seconds=self.max_age_seconds):
                return self.fallback("STALE_CONTEXT", "MARKET_TRUTH_VALIDATION")
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
                        "provider": response.provider, "model": response.model_version})
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
                pricing_version,cost_status,cached_tokens,reasoning_tokens,provider_request_id,
                provider_usage_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(idempotency_key) DO NOTHING""", (
                decision_id, key, f"signal:{context.signal_id}", context.telegram_id, context.signal_id,
                context.symbol, context.timeframe, context.market_timestamp, context.market_checksum,
                context.feature_checksum, response.provider, response.model, response.model_version,
                PROMPT_VERSION, mode.value, decision.regime, decision.direction, decision.confidence,
                decision.uncertainty, decision.action.value, decision.risk_multiplier, int(decision.abstention),
                _canonical(decision.supporting), _canonical(decision.conflicting),
                _canonical(decision.invalidations), decision.explanation, int(decision.valid), decision.code,
                latency_ms, response.input_tokens, response.output_tokens, str(response.estimated_cost_usd),
                response.raw_checksum, None if deterministic_accepted is None else int(deterministic_accepted),
                "ACCEPT" if deterministic_accepted else "REJECT" if deterministic_accepted is False else None,
                response.provider_protocol, SCHEMA_VERSION, SCHEMA_CHECKSUM, CONTEXT_VERSION,
                REQUEST_FORMAT_VERSION, response.requested_output_mode, response.effective_output_mode,
                response.downgrade_reason, decision.validation_stage, response.pricing_version,
                response.cost_status, response.cached_tokens, response.reasoning_tokens,
                response.provider_request_id, response.provider_usage_json, now,
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


class AITradingService:
    _semaphore: asyncio.Semaphore | None = None

    def __init__(self, provider: AIProvider | None = None, repository: AIDecisionRepository | None = None):
        self.provider = provider or build_ai_provider()
        self.repository = repository or AIDecisionRepository()
        self.context_builder = AIContextBuilder()
        self.timeout = max(0.1, float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "8")))
        self.max_tokens = max(64, int(os.getenv("AI_MAX_TOKENS", "800")))
        self.max_attempts = max(1, min(3, int(os.getenv("AI_PROVIDER_MAX_ATTEMPTS", "2"))))
        self.max_daily_user = max(0, int(os.getenv("AI_MAX_DAILY_REQUESTS_PER_USER", "25")))
        self.max_daily_global = max(0, int(os.getenv("AI_MAX_DAILY_REQUESTS", "500")))
        try:
            self.max_daily_cost = max(Decimal("0"), Decimal(os.getenv("AI_MAX_DAILY_COST_USD", "5")))
        except Exception:
            self.max_daily_cost = Decimal("0")
        self.max_context_chars = max(1000, int(os.getenv("AI_CONTEXT_MAX_CHARS", "30000")))
        self.validator = AIResponseValidator(max_age_seconds=int(os.getenv("AI_CONTEXT_MAX_AGE_SECONDS", "300")))
        self.capabilities = getattr(self.provider, "capabilities", AIProviderCapabilities(
            supports_json_object=True, supports_json_schema=True, supports_strict_schema=True,
            supports_temperature=True, supports_max_tokens=True, supports_usage_reporting=True,
            supports_retryable_idempotent_requests=True,
        ))
        if AITradingService._semaphore is None:
            AITradingService._semaphore = asyncio.Semaphore(max(1, int(os.getenv("AI_MAX_CONCURRENCY", "2"))))

    def _provider_available(self) -> bool:
        now = datetime.now(timezone.utc)
        with connect() as conn:
            row = conn.execute("SELECT * FROM ai_provider_state WHERE provider=?", (self.provider.name,)).fetchone()
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
        with connect() as conn:
            row = conn.execute("SELECT consecutive_failures FROM ai_provider_state WHERE provider=?", (self.provider.name,)).fetchone()
            failures = 0 if success else int(row["consecutive_failures"] or 0) + 1 if row else 1
            threshold = max(1, int(os.getenv("AI_CIRCUIT_BREAKER_FAILURES", "5")))
            state = "CLOSED" if success or failures < threshold else "OPEN"
            opened_until = (now + timedelta(seconds=max(30, int(os.getenv("AI_CIRCUIT_BREAKER_SECONDS", "300"))))).isoformat() if state == "OPEN" else None
            conn.execute("""INSERT INTO ai_provider_state(provider,state,consecutive_failures,opened_until,
                last_success_at,last_failure_at,last_error_code,updated_at) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(provider) DO UPDATE SET state=excluded.state,
                consecutive_failures=excluded.consecutive_failures,opened_until=excluded.opened_until,
                last_success_at=COALESCE(excluded.last_success_at,ai_provider_state.last_success_at),
                last_failure_at=COALESCE(excluded.last_failure_at,ai_provider_state.last_failure_at),
                last_error_code=excluded.last_error_code,updated_at=excluded.updated_at""",
                (self.provider.name, state, failures, opened_until, now.isoformat() if success else None,
                 None if success else now.isoformat(), code, now.isoformat()))

    def _usage(self, telegram_id: int | None) -> tuple[int, int, Decimal]:
        start = datetime.now(timezone.utc).date().isoformat()
        with connect() as conn:
            global_row = conn.execute("SELECT COUNT(*) n,COALESCE(SUM(estimated_cost_usd),0) cost FROM ai_decisions WHERE created_at>=?", (start,)).fetchone()
            user_row = conn.execute("SELECT COUNT(*) n FROM ai_decisions WHERE telegram_id=? AND created_at>=?", (telegram_id, start)).fetchone() if telegram_id is not None else {"n": 0}
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
        request_key = checksum({"signal": context.signal_id, "market": context.market_checksum,
                                "features": context.feature_checksum, "prompt": PROMPT_VERSION,
                                "provider": self.provider.name, "model": self.provider.model_version})
        existing = self.repository.latest(telegram_id=telegram_id, signal_id=signal_id)
        if (existing and existing.get("market_snapshot_checksum") == context.market_checksum
                and existing.get("feature_snapshot_checksum") == context.feature_checksum
                and existing.get("prompt_version") == PROMPT_VERSION
                and existing.get("provider") == self.provider.name
                and str(existing.get("model_version") or "") == str(self.provider.model_version or "")):
            return existing
        if not self._claim(request_key):
            return self.repository.latest(telegram_id=telegram_id, signal_id=signal_id)
        total, user_total, cost = self._usage(context.telegram_id)
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
        price_version = os.getenv("AI_PRICE_VERSION", "").strip()
        priced = bool(input_raw and output_raw and price_version)
        if self.provider.name != "disabled" and not priced and _env_bool("AI_REQUIRE_PRICING_FOR_REQUESTS", True):
            block = "COST_UNPRICED"
        try:
            input_rate = max(Decimal("0"), Decimal(input_raw or "0"))
            output_rate = max(Decimal("0"), Decimal(output_raw or "0"))
        except Exception:
            input_rate = output_rate = Decimal("0")
            block = "PRICING_CONFIGURATION_INVALID"
        priced_prompt_chars = prompt_chars + len(SYSTEM_PROMPT) + len(_canonical(RESPONSE_SCHEMA))
        estimated_upper_cost = (
            Decimal(math.ceil(priced_prompt_chars / 4)) * input_rate + Decimal(self.max_tokens) * output_rate
        ) / Decimal(1_000_000)
        if cost + estimated_upper_cost > self.max_daily_cost:
            block = "COST_LIMIT"
        self.repository.register_prompt()
        requested_output, effective_output, downgrade_reason = resolve_output_mode(self.capabilities)
        if effective_output is AIOutputMode.DISABLED and self.provider.name != "disabled":
            block = downgrade_reason or "PROVIDER_CAPABILITY_MISMATCH"
        started = time.perf_counter()
        response = AIProviderResponse(None, provider=self.provider.name,
                                      model=self.provider.model, model_version=self.provider.model_version,
                                      provider_protocol=getattr(self.provider, "protocol", "injected"),
                                      requested_output_mode=requested_output.value,
                                      effective_output_mode=effective_output.value,
                                      downgrade_reason=downgrade_reason,
                                      cost_status="PRICED" if priced else "UNPRICED",
                                      pricing_version=price_version or None)
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
                response = replace(response, downgrade_reason=downgrade_reason or response.downgrade_reason)
                decision = self.validator.validate(response.payload, context)
                self._provider_result(success=True)
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
        with connect() as conn:
            rows = conn.execute("""SELECT s.id,s.owner_telegram_id FROM signals s
                WHERE s.status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')
                AND NOT EXISTS(SELECT 1 FROM ai_decisions d WHERE d.signal_id=s.id
                    AND d.created_at>=s.updated_at) ORDER BY s.id LIMIT 10""").fetchall()
        # Snapshot-level duplicate suppression is enforced again by the ledger.
        processed = failed = 0
        for raw in rows:
            try:
                await self.service.analyze_signal(int(raw["id"]), telegram_id=raw["owner_telegram_id"])
                processed += 1
            except Exception:
                failed += 1
                logger.exception("AI shadow decision failed for signal_id=%s", raw["id"])
        return {"processed": processed, "failed": failed, "outcomes_attached": outcomes}

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

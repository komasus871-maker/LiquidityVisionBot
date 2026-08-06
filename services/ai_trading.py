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
from dataclasses import asdict, dataclass
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
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


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


class AIProvider(Protocol):
    name: str
    model: str
    model_version: str

    async def analyze(self, request: AIProviderRequest) -> AIProviderResponse: ...
    async def health(self) -> dict[str, Any]: ...


class DisabledAIProvider:
    name = "disabled"
    model = ""
    model_version = ""

    async def analyze(self, request: AIProviderRequest) -> AIProviderResponse:
        return AIProviderResponse(None, provider=self.name)

    async def health(self) -> dict[str, Any]:
        return {"provider": self.name, "status": "disabled"}


class StructuredHTTPAIProvider:
    """Configurable OpenAI-compatible structured HTTP boundary.

    Credentials are read only for the Authorization header and are never added
    to request context, returned errors, logs, or persistence records.
    """

    def __init__(self) -> None:
        self.name = os.getenv("AI_PROVIDER", "disabled").strip().lower()
        self.model = os.getenv("AI_MODEL", "").strip()
        self.model_version = os.getenv("AI_MODEL_VERSION", self.model).strip()
        self.endpoint = os.getenv("AI_PROVIDER_ENDPOINT", "").strip()
        self._api_key = os.getenv("AI_PROVIDER_API_KEY", "").strip()

    async def analyze(self, request: AIProviderRequest) -> AIProviderResponse:
        if not self.endpoint or not self.model or not self._api_key:
            raise RuntimeError("AI_PROVIDER_NOT_CONFIGURED")
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": _canonical(request.context)},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": request.max_tokens,
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        timeout = aiohttp.ClientTimeout(total=max(1.0, float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "8"))))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.endpoint, json=body, headers=headers) as response:
                if response.status >= 400:
                    raise RuntimeError(f"AI_PROVIDER_HTTP_{response.status}")
                data = await response.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        input_rate = Decimal(os.getenv("AI_INPUT_COST_PER_MILLION_USD", "0"))
        output_rate = Decimal(os.getenv("AI_OUTPUT_COST_PER_MILLION_USD", "0"))
        cost = (Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate) / Decimal(1_000_000)
        return AIProviderResponse(content, self.name, self.model, self.model_version,
                                  input_tokens, output_tokens, cost, checksum(content))

    async def health(self) -> dict[str, Any]:
        configured = bool(self.endpoint and self.model and self._api_key)
        return {"provider": self.name, "status": "configured" if configured else "misconfigured",
                "model": self.model, "model_version": self.model_version}


def build_ai_provider() -> AIProvider:
    return DisabledAIProvider() if os.getenv("AI_PROVIDER", "disabled").strip().lower() == "disabled" else StructuredHTTPAIProvider()


RESPONSE_SCHEMA = {
    "required": ["regime", "direction", "confidence", "uncertainty", "recommended_action",
                 "recommended_risk_multiplier", "abstention", "supporting_factors",
                 "conflicting_factors", "invalidation_conditions", "explanation"],
    "actions": [item.value for item in AIAction],
    "optional": ["symbol", "reference_price"],
}

PROMPT_VERSION = "ai-shadow-v1"
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


class AIResponseValidator:
    def __init__(self, *, min_risk: float = 0.0, max_risk: float = 1.0, max_age_seconds: int = 300):
        self.min_risk, self.max_risk = min_risk, max_risk
        self.max_age_seconds = max(1, max_age_seconds)

    def fallback(self, code: str) -> ValidatedDecision:
        return ValidatedDecision("UNKNOWN", "NEUTRAL", 0, 1, AIAction.ABSTAIN, 0, True,
                                 (), (code,), (), f"AI abstained: {code}", False, code)

    def validate(self, raw: dict[str, Any] | str | None, context: AIContext) -> ValidatedDecision:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(payload, dict):
                return self.fallback("MALFORMED_RESPONSE")
            missing = [key for key in RESPONSE_SCHEMA["required"] if key not in payload]
            if missing:
                return self.fallback("MISSING_REQUIRED_FIELD")
            allowed = set(RESPONSE_SCHEMA["required"]) | set(RESPONSE_SCHEMA["optional"])
            if set(payload) - allowed:
                return self.fallback("UNSUPPORTED_RESPONSE_FIELD")
            action = AIAction(str(payload["recommended_action"]).upper())
            confidence, uncertainty = float(payload["confidence"]), float(payload["uncertainty"])
            risk = float(payload["recommended_risk_multiplier"])
            if not all(math.isfinite(x) for x in (confidence, uncertainty, risk)):
                return self.fallback("INVALID_NUMERIC_VALUE")
            if not 0 <= confidence <= 100 or not 0 <= uncertainty <= 100:
                return self.fallback("CONFIDENCE_OUT_OF_RANGE")
            if not self.min_risk <= risk <= self.max_risk:
                return self.fallback("RISK_MULTIPLIER_OUT_OF_RANGE")
            direction = str(payload["direction"]).upper()
            if direction not in {"LONG", "SHORT", "NEUTRAL"}:
                return self.fallback("UNSUPPORTED_DIRECTION")
            supplied_symbol = str(payload.get("symbol", context.symbol)).upper()
            if supplied_symbol != context.symbol:
                return self.fallback("HALLUCINATED_SYMBOL")
            if "reference_price" in payload:
                reference = float(payload["reference_price"])
                truth = float(context.market.get("price") or 0)
                if not math.isfinite(reference) or reference <= 0 or truth <= 0 or abs(reference - truth) > max(1e-9, truth * 0.000001):
                    return self.fallback("IMPOSSIBLE_PRICE")
            for key in ("supporting_factors", "conflicting_factors", "invalidation_conditions"):
                if not isinstance(payload[key], list) or not all(isinstance(item, str) for item in payload[key]):
                    return self.fallback("INVALID_EVIDENCE_TYPE")
            if not isinstance(payload["abstention"], bool):
                return self.fallback("INVALID_ABSTENTION_TYPE")
            timestamp = datetime.fromisoformat(context.market_timestamp.replace("Z", "+00:00"))
            timestamp = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - timestamp > timedelta(seconds=self.max_age_seconds):
                return self.fallback("STALE_CONTEXT")
            factors = tuple(str(x)[:240] for x in payload["supporting_factors"] if str(x).strip())
            conflicts = tuple(str(x)[:240] for x in payload["conflicting_factors"] if str(x).strip())
            invalidations = tuple(str(x)[:240] for x in payload["invalidation_conditions"] if str(x).strip())
            if not factors and action in {AIAction.ACCEPT_REDUCED, AIAction.ACCEPT_STANDARD}:
                return self.fallback("MISSING_REQUIRED_EVIDENCE")
            abstention = bool(payload["abstention"])
            if action is AIAction.ABSTAIN:
                abstention, risk = True, 0
            return ValidatedDecision(str(payload["regime"])[:80], direction, confidence, uncertainty,
                                     action, risk, abstention, factors, conflicts, invalidations,
                                     str(payload["explanation"])[:1000], True, "VALID")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return self.fallback("MALFORMED_RESPONSE")


class AIDecisionRepository:
    def register_prompt(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO ai_prompt_versions(prompt_version,prompt_checksum,system_prompt,
                response_schema_json,active,created_at) VALUES(?,?,?,?,1,?)
                ON CONFLICT(prompt_version) DO NOTHING""",
                (PROMPT_VERSION, checksum(SYSTEM_PROMPT), SYSTEM_PROMPT, _canonical(RESPONSE_SCHEMA), now))

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
                raw_response_checksum,deterministic_accepted,deterministic_action,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                "ACCEPT" if deterministic_accepted else "REJECT" if deterministic_accepted is False else None, now,
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
        self.max_daily_cost = Decimal(os.getenv("AI_MAX_DAILY_COST_USD", "5"))
        self.max_context_chars = max(1000, int(os.getenv("AI_CONTEXT_MAX_CHARS", "30000")))
        self.validator = AIResponseValidator(max_age_seconds=int(os.getenv("AI_CONTEXT_MAX_AGE_SECONDS", "300")))
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
        input_rate = max(Decimal("0"), Decimal(os.getenv("AI_INPUT_COST_PER_MILLION_USD", "0")))
        output_rate = max(Decimal("0"), Decimal(os.getenv("AI_OUTPUT_COST_PER_MILLION_USD", "0")))
        estimated_upper_cost = (
            Decimal(math.ceil(prompt_chars / 4)) * input_rate + Decimal(self.max_tokens) * output_rate
        ) / Decimal(1_000_000)
        if cost + estimated_upper_cost > self.max_daily_cost:
            block = "COST_LIMIT"
        self.repository.register_prompt()
        started = time.perf_counter()
        response = AIProviderResponse(None, provider=self.provider.name,
                                      model=self.provider.model, model_version=self.provider.model_version)
        if block or self.provider.name == "disabled" or not self._provider_available():
            decision = self.validator.fallback(block or ("PROVIDER_DISABLED" if self.provider.name == "disabled" else "CIRCUIT_OPEN"))
        else:
            request = AIProviderRequest(SYSTEM_PROMPT, PROMPT_VERSION, prompt_payload,
                                        RESPONSE_SCHEMA, self.max_tokens)
            try:
                assert self._semaphore is not None
                last_error: Exception | None = None
                for attempt in range(self.max_attempts):
                    try:
                        async with self._semaphore:
                            response = await asyncio.wait_for(self.provider.analyze(request), timeout=self.timeout)
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt + 1 < self.max_attempts:
                            await asyncio.sleep(min(0.5, 0.1 * (2 ** attempt)))
                if last_error is not None:
                    raise last_error
                decision = self.validator.validate(response.payload, context)
                self._provider_result(success=True)
            except asyncio.TimeoutError:
                decision = self.validator.fallback("PROVIDER_TIMEOUT")
                self._provider_result(success=False, code=decision.code)
            except Exception:
                decision = self.validator.fallback("PROVIDER_FAILURE")
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

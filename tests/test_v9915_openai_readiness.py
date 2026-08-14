from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest


def _payload(**overrides):
    value = {
        "regime": "CERTIFICATION", "direction": "NEUTRAL", "confidence": 25,
        "uncertainty": 75, "recommended_action": "ABSTAIN",
        "recommended_risk_multiplier": 0, "abstention": True,
        "supporting_factors": [{"evidence_id": "bounded_context", "statement": "bounded synthetic context", "strength": 100}],
        "conflicting_factors": [{"evidence_id": "not_live_data", "statement": "not live market data", "severity": "CRITICAL"}],
        "invalidation_conditions": ["certification completes"],
        "explanation": "No trading action is appropriate for a certification fixture.",
        "market_regimes": ["CERTIFICATION"], "opportunity_quality": 0,
        "evidence_ranking": [{"evidence_id": "bounded_context", "rank": 1}],
        "uncertainty_explanation": "Synthetic context is intentionally uncertain.",
        "symbol": None, "reference_price": None,
    }
    value.update(overrides)
    return value


@pytest.fixture()
def readiness_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "readiness.db")
    monkeypatch.setenv("AI_TRADING_MODE", "AI_OBSERVE")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_PROVIDER_PROTOCOL", "responses")
    monkeypatch.setenv("AI_PROVIDER_ENDPOINT", "https://api.openai.com/v1/responses")
    monkeypatch.setenv("AI_PROVIDER_API_KEY", "test-secret-never-rendered")
    monkeypatch.setenv("AI_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("AI_MODEL_VERSION", "gpt-5.6-terra")
    monkeypatch.setenv("AI_STRUCTURED_OUTPUT_MODE", "json_schema")
    monkeypatch.setenv("AI_STRICT_SCHEMA_REQUIRED", "true")
    monkeypatch.setenv("AI_ALLOW_JSON_OBJECT_FALLBACK", "false")
    monkeypatch.setenv("AI_SUPPORTS_JSON_OBJECT", "false")
    monkeypatch.setenv("AI_SUPPORTS_JSON_SCHEMA", "true")
    monkeypatch.setenv("AI_SUPPORTS_STRICT_SCHEMA", "true")
    monkeypatch.setenv("AI_SUPPORTS_TEMPERATURE", "false")
    monkeypatch.setenv("AI_SUPPORTS_MAX_TOKENS", "false")
    monkeypatch.setenv("AI_SUPPORTS_MAX_COMPLETION_TOKENS", "false")
    monkeypatch.setenv("AI_SUPPORTS_MAX_OUTPUT_TOKENS", "true")
    monkeypatch.setenv("AI_SUPPORTS_USAGE_REPORTING", "true")
    monkeypatch.setenv("AI_SUPPORTS_REQUEST_ID", "true")
    monkeypatch.setenv("AI_SUPPORTS_REASONING_MODELS", "true")
    monkeypatch.setenv("AI_SUPPORTS_RETRYABLE_IDEMPOTENT_REQUESTS", "false")
    monkeypatch.setenv("AI_REASONING_EFFORT", "low")
    monkeypatch.setenv("AI_PRICE_VERSION", "test-prices-v1")
    monkeypatch.setenv("AI_INPUT_COST_PER_MILLION_USD", "2.5")
    monkeypatch.setenv("AI_CACHED_INPUT_COST_PER_MILLION_USD", "0.25")
    monkeypatch.setenv("AI_CACHE_WRITE_COST_PER_MILLION_USD", "3.125")
    monkeypatch.setenv("AI_OUTPUT_COST_PER_MILLION_USD", "15")
    monkeypatch.setenv("AI_REQUIRE_PRICING_FOR_REQUESTS", "true")
    monkeypatch.setenv("AI_REQUEST_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("AI_PROVIDER_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AI_MAX_DAILY_REQUESTS", "100")
    monkeypatch.setenv("AI_MAX_DAILY_REQUESTS_PER_USER", "10")
    monkeypatch.setenv("AI_MAX_DAILY_COST_USD", "2")
    monkeypatch.setenv("AI_MAX_TOKENS", "600")
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("AI_OBSERVE_QUEUE_DEPTH", "10")
    monkeypatch.setenv("AI_CERTIFICATION_REPEAT_SUPPRESSION_SECONDS", "60")
    from database.database import connect, create_tables
    create_tables()
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("""INSERT INTO signals(id,owner_telegram_id,symbol,timeframe,side,status,created_at,updated_at,
            entry,stop,tp1,tp2,tp3,rr,confidence,bull_score,bear_score,recommendation,setup_key,
            features_json,reasons_json,current_price,max_profit_pct,max_drawdown_pct)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            1501, 7, "BTCUSDT", "1h", "LONG", "ACTIVE", now, now, 100, 95, 110, 115,
            120, 2, 70, 70, 30, "READY", "v9915", "{}", "[]", 100, 0, 0))
    return tmp_path


def _request():
    from services.ai_trading import (AIOutputMode, AIProviderRequest, RESPONSE_SCHEMA,
                                     SCHEMA_CHECKSUM, SCHEMA_VERSION)
    return AIProviderRequest("system", "prompt", {"symbol": "CERTIFICATION"}, RESPONSE_SCHEMA,
        192, AIOutputMode.STRICT_JSON_SCHEMA, AIOutputMode.STRICT_JSON_SCHEMA,
        SCHEMA_VERSION, SCHEMA_CHECKSUM)


@pytest.mark.asyncio
async def test_chat_json_string_and_dictionary_are_normalized(readiness_db, monkeypatch):
    from services.ai_trading import ChatCompletionsAIProvider
    monkeypatch.setenv("AI_PROVIDER_PROTOCOL", "chat_completions")
    monkeypatch.setenv("AI_PROVIDER_ENDPOINT", "https://api.openai.com/v1/chat/completions")
    monkeypatch.setenv("AI_SUPPORTS_MAX_OUTPUT_TOKENS", "false")
    monkeypatch.setenv("AI_SUPPORTS_MAX_COMPLETION_TOKENS", "true")

    class Provider(ChatCompletionsAIProvider):
        def __init__(self, content):
            super().__init__()
            self.content = content

        async def _post(self, body):
            return ({"id": "chat-1", "model": self.model,
                     "choices": [{"message": {"content": self.content}}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, "header-chat-1")

    for content in (json.dumps(_payload()), _payload()):
        response = await Provider(content).analyze(_request())
        assert response.payload == _payload()
        assert response.extraction_valid and response.structured_text
        assert response.provider_request_id == "header-chat-1"
        assert "choices" not in response.__dict__ if hasattr(response, "__dict__") else True


@pytest.mark.parametrize("output", [
    [{"type": "reasoning", "summary": []},
     {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "{}"}]}],
    [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "{}"}]},
     {"type": "reasoning", "summary": []}],
    [{"type": "reasoning"}, {"type": "reasoning"},
     {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "{}"}]}],
])
def test_responses_finds_final_message_independent_of_reasoning_order(output):
    from services.ai_trading import OpenAIResponsesAIProvider
    content, error = OpenAIResponsesAIProvider._extract_content({"output": output})
    assert content == "{}" and error is None


@pytest.mark.parametrize("data,expected,path", [
    ({"status": "completed", "output_text": "{}"}, "{}", "responses.output_text"),
    ({"status": "completed", "output_parsed": {"ok": True}}, {"ok": True},
     "responses.output_parsed"),
    ({"status": "completed", "output": [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "not retained"}]},
        {"type": "message", "role": "assistant", "status": "completed", "content": [
            {"type": "output_text", "text": "{\"ok\":", "annotations": []},
            {"type": "output_text", "text": "true}", "annotations": []},
        ]},
    ]}, '{"ok":true}', "responses.output[].message.content[].output_text"),
    ({"status": "completed", "output": [
        {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "parsed": {"ok": True}, "text": "ignored"},
        ]},
    ]}, {"ok": True}, "responses.output[].message.content[].parsed"),
    ({"status": "completed", "output": [
        {"type": "output_text", "text": "{}"},
    ]}, "{}", "responses.output[].output_text"),
    ({"status": "completed", "output": [
        {"type": "message", "role": "assistant", "content": "{}"},
    ]}, "{}", "responses.output[].message.content"),
])
def test_responses_documented_and_sdk_layouts_share_one_normalizer(data, expected, path):
    from services.ai_trading import OpenAIResponsesAIProvider
    result = OpenAIResponsesAIProvider._extract_content(data)
    assert result.content == expected and result.code is None and result.path == path


@pytest.mark.parametrize("reason,code", [
    ("max_output_tokens", "RESPONSES_INCOMPLETE_MAX_OUTPUT_TOKENS"),
    ("content_filter", "RESPONSES_INCOMPLETE_CONTENT_FILTER"),
    ("future_reason", "RESPONSES_INCOMPLETE_UNKNOWN"),
])
def test_responses_incomplete_reason_is_deterministic(reason, code):
    from services.ai_trading import OpenAIResponsesAIProvider
    result = OpenAIResponsesAIProvider._extract_content({
        "status": "incomplete", "incomplete_details": {"reason": reason},
        "output": [{"type": "reasoning", "summary": []}],
    })
    assert result.content is None and result.code == code
    assert result.stage == "PROVIDER_COMPLETION" and result.incomplete_reason == reason


def test_final_assistant_item_governs_and_reasoning_is_never_extracted():
    from services.ai_trading import OpenAIResponsesAIProvider
    result = OpenAIResponsesAIProvider._extract_content({"output": [
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "{\"old\":true}"}]},
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "private"}]},
        {"type": "message", "role": "assistant",
         "content": [{"type": "refusal", "refusal": "cannot comply"}]},
    ]})
    assert result.content is None and result.code == "PROVIDER_REFUSAL"
    assert "private" not in repr(result)


@pytest.mark.parametrize("data,code", [
    ({"output": [{"type": "reasoning"}]}, "RESPONSES_MESSAGE_MISSING"),
    ({"output": [{"type": "message", "role": "assistant", "content": {}}]},
     "RESPONSES_MESSAGE_CONTENT_INVALID"),
    ({"output": []}, "RESPONSES_MESSAGE_MISSING"),
    ({"output": [{"type": "message", "role": "assistant",
                  "content": [{"type": "refusal", "refusal": "cannot"}]}]}, "PROVIDER_REFUSAL"),
])
def test_responses_missing_malformed_and_refusal_are_normalized(data, code):
    from services.ai_trading import OpenAIResponsesAIProvider
    content, error = OpenAIResponsesAIProvider._extract_content(data)
    assert content is None and error == code


@pytest.mark.asyncio
async def test_valid_chat_json_string_certifies_through_production_pipeline(readiness_db, monkeypatch):
    from services.ai_operations import AIProviderCertificationService
    from services.ai_trading import ChatCompletionsAIProvider
    monkeypatch.setenv("AI_PROVIDER_PROTOCOL", "chat_completions")
    monkeypatch.setenv("AI_PROVIDER_ENDPOINT", "https://api.openai.com/v1/chat/completions")
    monkeypatch.setenv("AI_SUPPORTS_MAX_OUTPUT_TOKENS", "false")
    monkeypatch.setenv("AI_SUPPORTS_MAX_COMPLETION_TOKENS", "true")

    class Provider(ChatCompletionsAIProvider):
        async def _post(self, body):
            return ({"id": "cert-chat", "model": self.model,
                     "choices": [{"message": {"content": json.dumps(_payload())}}],
                     "usage": {"prompt_tokens": 30, "completion_tokens": 20}}, None)

    report = await AIProviderCertificationService(Provider()).certify(7)
    assert report["status"] == "PASSED"
    assert report["checks"]["schema"] and report["checks"]["semantic_valid"]
    assert report["checks"]["paid_provider_request"] and report["validation_stage"] == "COMPLETE"


class _CertificationProvider:
    name, protocol = "compatible", "responses"
    endpoint, _api_key = "https://provider.example/v1/responses", "secret"
    model, model_version = "reasoning-model", "reasoning-model-v1"

    def __init__(self, response_kind="valid", delay=0):
        from services.ai_trading import AIProviderCapabilities
        self.capabilities = AIProviderCapabilities(
            supports_json_schema=True, supports_strict_schema=True,
            supports_usage_reporting=True, supports_request_id=True,
            supports_reasoning_models=True, supports_max_output_tokens=True)
        self.response_kind, self.delay, self.calls = response_kind, delay, 0
        self.last_max_tokens = None

    async def analyze(self, request):
        from services.ai_trading import AIProviderResponse
        self.calls += 1
        self.last_max_tokens = request.max_tokens
        if self.delay:
            await asyncio.sleep(self.delay)
        payload = _payload()
        usage_valid, cost = True, "PRICED"
        if self.response_kind == "schema":
            payload = {"regime": "missing everything else"}
        elif self.response_kind == "semantic":
            payload = _payload(direction="NEUTRAL", recommended_action="ACCEPT_STANDARD",
                               recommended_risk_multiplier=1, abstention=False)
        elif self.response_kind == "usage":
            usage_valid = False
        elif self.response_kind == "unpriced":
            cost = "UNPRICED"
        return AIProviderResponse(
            payload, self.name, self.model, self.model_version, 20, 10, Decimal("0.001"),
            provider_request_id="cert-request", provider_protocol=self.protocol,
            requested_output_mode="json_schema", effective_output_mode="json_schema",
            cost_status=cost, pricing_version="test-prices-v1", usage_valid=usage_valid)


@pytest.mark.asyncio
async def test_terra_certification_reserves_reasoning_and_structured_output_budget(readiness_db,
                                                                                   monkeypatch):
    from services.ai_operations import AIProviderCertificationService
    monkeypatch.delenv("AI_CERTIFICATION_MAX_TOKENS", raising=False)
    provider = _CertificationProvider()
    report = await AIProviderCertificationService(provider).certify()
    assert report["status"] == "PASSED"
    assert provider.last_max_tokens == 1200
    assert report["checks"]["max_output_tokens"] == 1200


@pytest.mark.asyncio
async def test_certification_accepts_192_output_tokens_without_weakening_bounds(readiness_db,
                                                                                monkeypatch):
    from services.ai_operations import AIConfigurationValidator, AIProviderCertificationService
    monkeypatch.setenv("AI_CERTIFICATION_MAX_TOKENS", "192")
    provider = _CertificationProvider()
    validation = AIConfigurationValidator().validate(provider)
    report = await AIProviderCertificationService(provider).certify()
    assert validation.valid
    assert report["status"] == "PASSED"
    assert provider.last_max_tokens == 192


@pytest.mark.parametrize("value", ["127", "192.5", "4097", "invalid"])
def test_certification_output_token_validation_remains_strict(readiness_db, monkeypatch, value):
    from services.ai_operations import AIConfigurationValidator
    monkeypatch.setenv("AI_CERTIFICATION_MAX_TOKENS", value)
    validation = AIConfigurationValidator().validate(_CertificationProvider())
    assert "AI_CERTIFICATION_MAX_TOKENS_INVALID" in validation.errors


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,code,stage", [
    ("schema", "SCHEMA_VALIDATION_FAILED", "JSON_SCHEMA_VALIDATION"),
    ("semantic", "DIRECTION_CONFLICT", "SEMANTIC_VALIDATION"),
    ("usage", "CERTIFICATION_USAGE_MISSING", "USAGE_VALIDATION"),
    ("unpriced", "CERTIFICATION_COST_UNPRICED", "PRICING_VALIDATION"),
])
async def test_certification_records_normalized_failure_stage(readiness_db, kind, code, stage):
    from services.ai_operations import AIProviderCertificationService
    report = await AIProviderCertificationService(_CertificationProvider(kind)).certify()
    assert report["status"] == "FAILED"
    assert report["failure_code"] == code and report["validation_stage"] == stage


@pytest.mark.asyncio
async def test_certification_and_production_observation_share_semantic_validator(
        readiness_db, monkeypatch):
    import services.ai_trading as trading
    from services.ai_operations import AIProviderCertificationService

    seen_signal_ids = []
    real_validate = trading.validate_provider_response

    def observed_validate(response, context, validator=None):
        seen_signal_ids.append(context.signal_id)
        return real_validate(response, context, validator)

    monkeypatch.setattr(trading, "validate_provider_response", observed_validate)
    provider = _CertificationProvider()
    certification = await AIProviderCertificationService(provider).certify()
    trading.AITradingService._semaphore = None
    observation = await trading.AITradingService(provider).analyze_signal(1501, telegram_id=7)

    assert certification["status"] == "PASSED"
    assert observation["validation_code"] == "VALID"
    assert seen_signal_ids == [0, 1501]


@pytest.mark.asyncio
async def test_stale_schema_version_fails_before_any_paid_certification_request(
        readiness_db, monkeypatch):
    from services.ai_operations import AIProviderCertificationService

    monkeypatch.setenv("AI_SCHEMA_VERSION", "ai-decision-v2")
    provider = _CertificationProvider()
    report = await AIProviderCertificationService(provider).certify()

    assert report["status"] == "CONFIG_INVALID"
    assert report["failure_code"] == "AI_SCHEMA_VERSION_UNSUPPORTED"
    assert not report["checks"]["paid_provider_request"]
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_certification_repeat_is_durably_suppressed(readiness_db):
    from services.ai_operations import AIProviderCertificationService
    provider = _CertificationProvider(delay=.05)
    service = AIProviderCertificationService(provider)
    first, second = await asyncio.gather(service.certify(7), service.certify(7))
    assert provider.calls == 1
    assert sum(bool(item.get("duplicate_suppressed")) for item in (first, second)) == 1


@pytest.mark.asyncio
async def test_expiry_and_identity_change_invalidate_certification(readiness_db, monkeypatch):
    from database.database import connect
    from services.ai_operations import (AIControlRepository, AIProviderCertificationService,
                                        provider_identity)
    provider = _CertificationProvider()
    report = await AIProviderCertificationService(provider).certify()
    assert report["status"] == "PASSED"
    identity = provider_identity(provider)
    with connect() as conn:
        conn.execute("UPDATE ai_provider_certifications SET expires_at=? WHERE identity_checksum=?",
                     ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                      identity["identity_checksum"]))
    assert AIControlRepository().certification_state(provider)["state"] == "EXPIRED"
    with connect() as conn:
        conn.execute("UPDATE ai_provider_certifications SET expires_at=? WHERE identity_checksum=?",
                     ((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                      identity["identity_checksum"]))
    monkeypatch.setenv("AI_REASONING_EFFORT", "medium")
    assert AIControlRepository().certification_state(provider)["state"] == "IDENTITY_CHANGED"


@pytest.mark.asyncio
async def test_promotion_and_metrics_use_only_exact_identity(readiness_db, monkeypatch):
    from database.database import connect
    from services.ai_evaluation import AIEvaluationService
    from services.ai_operations import (AIGovernanceState, AIProviderCertificationService,
                                        promotion_evidence, provider_identity)
    from services.ai_trading import (AIDecisionRepository, AIProviderResponse, AIResponseValidator,
                                     AITradingMode, AIContextBuilder)
    provider = _CertificationProvider()
    assert (await AIProviderCertificationService(provider).certify())["status"] == "PASSED"
    identity = provider_identity(provider)
    context = AIContextBuilder().from_signal(1501, telegram_id=7)
    decision_payload = _payload(regime="TREND", direction="LONG", confidence=70, uncertainty=30,
        recommended_action="ACCEPT_STANDARD", recommended_risk_multiplier=1, abstention=False,
        symbol="BTCUSDT", reference_price=100)
    decision = AIResponseValidator().validate(decision_payload, context)
    repository = AIDecisionRepository()
    for response in (
        AIProviderResponse(decision_payload, provider.name, provider.model, provider.model_version,
                           identity_checksum=identity["identity_checksum"], provider_invoked=True),
        AIProviderResponse(decision_payload, "other", "other-model", "other-v1",
                           identity_checksum="different-model-prompt-schema-identity", provider_invoked=True),
        AIProviderResponse(decision_payload, "disabled", "", ""),
    ):
        repository.save(context=context, response=response, decision=decision,
                        mode=AITradingMode.AI_OBSERVE, latency_ms=5)
    current = AIEvaluationService().metrics(identity_checksum=identity["identity_checksum"])
    global_history = AIEvaluationService().metrics()
    assert current["decision_count"] == 1 and global_history["decision_count"] == 3
    assert global_history["legacy_classification"]["legacy_disabled"] == 1
    monkeypatch.setenv("AI_PROMOTION_MIN_SHADOW_DECISIONS", "1")
    evidence = promotion_evidence(provider, AIGovernanceState.SHADOW_CERTIFIED)
    assert evidence["eligible"] and evidence["evidence"]["decision_count"] == 1
    with connect() as conn:
        row = conn.execute("SELECT * FROM ai_governance_evidence ORDER BY id DESC LIMIT 1").fetchone()
    assert row["decision_count"] == 1 and row["schema_valid_count"] == 1


def test_gpt56_responses_and_chat_build_only_declared_parameters(readiness_db, monkeypatch):
    from services.ai_trading import ChatCompletionsAIProvider, OpenAIResponsesAIProvider
    responses = OpenAIResponsesAIProvider().build_request(_request())
    assert responses["reasoning"] == {"effort": "low"}
    assert responses["max_output_tokens"] == 192
    assert "messages" not in responses and "temperature" not in responses

    monkeypatch.setenv("AI_PROVIDER_PROTOCOL", "chat_completions")
    monkeypatch.setenv("AI_PROVIDER_ENDPOINT", "https://api.openai.com/v1/chat/completions")
    monkeypatch.setenv("AI_SUPPORTS_MAX_OUTPUT_TOKENS", "false")
    monkeypatch.setenv("AI_SUPPORTS_MAX_COMPLETION_TOKENS", "true")
    chat = ChatCompletionsAIProvider().build_request(_request())
    assert chat["max_completion_tokens"] == 192
    assert "max_tokens" not in chat and "temperature" not in chat


@pytest.mark.asyncio
async def test_gpt56_cache_write_usage_and_cost_are_not_omitted(readiness_db):
    from services.ai_trading import OpenAIResponsesAIProvider

    class Provider(OpenAIResponsesAIProvider):
        async def _post(self, body):
            return ({"id": "cache-write-response", "model": self.model,
                     "output_text": json.dumps(_payload()),
                     "usage": {"input_tokens": 100, "output_tokens": 20,
                               "input_tokens_details": {
                                   "cached_tokens": 40, "cache_write_tokens": 90},
                               "output_tokens_details": {"reasoning_tokens": 5}}}, None)

    response = await Provider().analyze(_request())
    assert response.cached_tokens == 40 and response.cache_write_tokens == 90
    assert response.reasoning_tokens == 5 and response.usage_valid
    assert json.loads(response.provider_usage_json)["cache_write_tokens"] == 90
    assert response.cost_status == "PRICED"
    assert response.estimated_cost_usd == Decimal("0.00061625")


@pytest.mark.asyncio
async def test_cache_write_rate_is_reserved_by_daily_cost_guard(readiness_db, monkeypatch):
    from services.ai_trading import AIProviderCapabilities, AIProviderResponse, AITradingService

    class Provider:
        name, protocol, endpoint = "injected", "injected", ""
        model = model_version = "cache-write-model"
        capabilities = AIProviderCapabilities(
            supports_json_schema=True, supports_strict_schema=True,
            supports_max_tokens=True, supports_usage_reporting=True)

        def __init__(self):
            self.calls = 0

        async def analyze(self, request):
            self.calls += 1
            return AIProviderResponse(_payload(), self.name, self.model, self.model_version)

    monkeypatch.setenv("AI_INPUT_COST_PER_MILLION_USD", "1")
    monkeypatch.setenv("AI_CACHE_WRITE_COST_PER_MILLION_USD", "1000")
    monkeypatch.setenv("AI_OUTPUT_COST_PER_MILLION_USD", "0")
    monkeypatch.setenv("AI_MAX_DAILY_COST_USD", "0.1")
    AITradingService._semaphore = None
    provider = Provider()
    row = await AITradingService(provider).analyze_signal(1501, telegram_id=7)
    assert provider.calls == 0
    assert row["validation_code"] == "COST_LIMIT"


@pytest.mark.asyncio
async def test_observation_queue_is_bounded_and_never_touches_execution(readiness_db, monkeypatch):
    from database.database import connect
    from services.ai_trading import (AIProviderCapabilities, AIProviderResponse, AIShadowWorker,
                                     AITradingService)
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        for signal_id, symbol in ((1502, "ETHUSDT"), (1503, "SOLUSDT")):
            conn.execute("""INSERT INTO signals(id,owner_telegram_id,symbol,timeframe,side,status,created_at,updated_at,
                entry,stop,tp1,tp2,tp3,rr,confidence,bull_score,bear_score,recommendation,setup_key,
                features_json,reasons_json,current_price,max_profit_pct,max_drawdown_pct)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                signal_id, 7, symbol, "1h", "LONG", "ACTIVE", now, now, 100, 95, 110, 115,
                120, 2, 70, 70, 30, "READY", f"v9915-{signal_id}", "{}", "[]", 100, 0, 0))

    class Provider:
        name, protocol = "injected", "injected"
        endpoint, model, model_version = "", "fake", "fake-v1"
        capabilities = AIProviderCapabilities(supports_json_schema=True, supports_strict_schema=True,
            supports_max_tokens=True, supports_usage_reporting=True)

        def __init__(self):
            self.calls = 0

        async def analyze(self, request):
            self.calls += 1
            return AIProviderResponse(_payload(regime="TREND", direction="LONG", symbol=None),
                self.name, self.model, self.model_version)

    monkeypatch.setenv("AI_OBSERVE_QUEUE_DEPTH", "1")
    AITradingService._semaphore = None
    provider = Provider()
    worker = AIShadowWorker()
    worker.service = AITradingService(provider)
    result = await worker.check_once()
    assert result["processed"] == 1 and result["dropped"] == 2 and provider.calls == 1
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM ai_observation_events WHERE reason_code='OBSERVATION_QUEUE_FULL'").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) n FROM paper_execution_orders").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM live_executions").fetchone()["n"] == 0


@pytest.mark.asyncio
async def test_returning_to_prior_identity_does_not_repeat_paid_request(readiness_db):
    from services.ai_trading import AIProviderCapabilities, AIProviderResponse, AITradingService

    class Provider:
        protocol, endpoint = "injected", ""
        capabilities = AIProviderCapabilities(supports_json_schema=True, supports_strict_schema=True,
            supports_max_tokens=True, supports_usage_reporting=True)

        def __init__(self, name):
            self.name = name
            self.model = self.model_version = f"{name}-model"
            self.calls = 0

        async def analyze(self, request):
            self.calls += 1
            return AIProviderResponse(_payload(regime="TREND", direction="LONG"), self.name,
                                      self.model, self.model_version)

    AITradingService._semaphore = None
    provider_a, provider_b = Provider("identity-a"), Provider("identity-b")
    service_a, service_b = AITradingService(provider_a), AITradingService(provider_b)
    first_a = await service_a.analyze_signal(1501, telegram_id=7)
    await service_b.analyze_signal(1501, telegram_id=7)
    second_a = await service_a.analyze_signal(1501, telegram_id=7)
    assert first_a["decision_id"] == second_a["decision_id"]
    assert provider_a.calls == 1 and provider_b.calls == 1


def test_invalid_bounds_disable_provider_without_crashing_service(readiness_db, monkeypatch):
    from services.ai_operations import AIConfigurationValidator
    from services.ai_trading import AITradingService, OpenAIResponsesAIProvider
    monkeypatch.setenv("AI_REQUEST_TIMEOUT_SECONDS", "not-a-number")
    monkeypatch.setenv("AI_MAX_DAILY_COST_USD", "NaN")
    provider = OpenAIResponsesAIProvider()
    validation = AIConfigurationValidator().validate(provider)
    service = AITradingService(provider)
    assert not validation.valid and "AI_REQUEST_TIMEOUT_SECONDS_INVALID" in validation.errors
    assert service._activation_block() == "AI_REQUEST_TIMEOUT_SECONDS_INVALID"


def test_persisted_snapshot_checksum_has_no_wall_clock_freshness(readiness_db):
    from services.ai_trading import AIContextBuilder
    first = AIContextBuilder().from_signal(1501, telegram_id=7)
    second = AIContextBuilder().from_signal(1501, telegram_id=7)
    assert first.feature_checksum == second.feature_checksum
    assert "data_freshness_seconds" not in first.features


def test_additive_migration_is_idempotent_and_postgres_placeholder_safe(readiness_db):
    from database.database import DBConnection, connect, create_tables
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: create_tables(), range(2)))
    with connect() as conn:
        decision_columns = {row[1] for row in conn.execute("PRAGMA table_info(ai_decisions)").fetchall()}
        cert_columns = {row[1] for row in conn.execute("PRAGMA table_info(ai_provider_certifications)").fetchall()}
        request_event_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(ai_provider_request_events)").fetchall()}
        queue_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(ai_observation_queue_snapshots)").fetchall()}
    assert {"provider_identity_checksum", "reasoning_effort", "extraction_code", "provider_invoked",
            "cache_write_tokens"} <= decision_columns
    assert {"started_at", "completed_at", "validation_stage", "provider_request_id",
            "cache_write_tokens"} <= cert_columns
    assert {"queue_wait_ms", "validation_stage", "validation_code", "extraction_code",
            "schema_valid", "semantic_valid"} <= request_event_columns
    assert "validation_failed" in queue_columns
    assert DBConnection._translate("VALUES(?,?) ON CONFLICT(x) DO UPDATE SET y=?") == \
           "VALUES(%s,%s) ON CONFLICT(x) DO UPDATE SET y=%s"


@pytest.mark.asyncio
async def test_telegram_provider_output_is_scoped_and_secret_free(readiness_db):
    from handlers.ai_trading import ai_provider

    class Message:
        from_user = SimpleNamespace(id=7)

        def __init__(self):
            self.answers = []

        async def answer(self, value):
            self.answers.append(value)

    message = Message()
    await ai_provider(message)
    output = "\n".join(message.answers)
    assert "Current identity decisions" in output and "Certification:" in output
    assert "test-secret-never-rendered" not in output and "/v1/responses" in output

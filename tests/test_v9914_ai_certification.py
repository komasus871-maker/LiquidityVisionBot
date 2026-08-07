from __future__ import annotations

import json
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest


@pytest.fixture()
def operations_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "operations.db")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_PROVIDER_PROTOCOL", "responses")
    monkeypatch.setenv("AI_PROVIDER_ENDPOINT", "https://api.openai.com/v1/responses")
    monkeypatch.setenv("AI_PROVIDER_API_KEY", "never-logged")
    monkeypatch.setenv("AI_MODEL", "gpt-test")
    monkeypatch.setenv("AI_PRICE_VERSION", "test-v1")
    monkeypatch.setenv("AI_INPUT_COST_PER_MILLION_USD", "1")
    monkeypatch.setenv("AI_CACHED_INPUT_COST_PER_MILLION_USD", "0.5")
    monkeypatch.setenv("AI_CACHE_WRITE_COST_PER_MILLION_USD", "1.25")
    monkeypatch.setenv("AI_OUTPUT_COST_PER_MILLION_USD", "2")
    from database.database import create_tables
    create_tables()


def _payload():
    return {
        "regime": "TREND", "direction": "NEUTRAL", "confidence": 50, "uncertainty": 50,
        "recommended_action": "ABSTAIN", "recommended_risk_multiplier": 0,
        "abstention": True, "supporting_factors": ["certification"],
        "conflicting_factors": ["synthetic context"], "invalidation_conditions": ["none"],
        "explanation": "Certification response only.",
        "market_regimes": ["CERTIFICATION"], "opportunity_quality": 0,
        "evidence_ranking": ["certification"],
        "uncertainty_explanation": "Synthetic context is not a market observation.",
        "symbol": None, "reference_price": None,
    }


def test_configuration_validation_and_identity_are_secret_free(operations_db):
    from services.ai_operations import AIConfigurationValidator, provider_identity
    from services.ai_trading import OpenAIResponsesAIProvider
    provider = OpenAIResponsesAIProvider()
    result = AIConfigurationValidator().validate(provider)
    identity = provider_identity(provider)
    assert result.valid
    assert identity["endpoint"] == "https://api.openai.com/v1/responses"
    assert "never-logged" not in json.dumps(identity)


@pytest.mark.asyncio
async def test_successful_certification_is_persisted_and_expires(operations_db):
    from services.ai_operations import AIControlRepository, AIProviderCertificationService, provider_identity
    from services.ai_trading import AIProviderCapabilities, AIProviderResponse

    class Provider:
        name, protocol = "openai", "responses"
        endpoint, _api_key = "https://api.openai.com/v1/responses", "secret"
        model, model_version = "gpt-test", "gpt-test-2026"
        capabilities = AIProviderCapabilities(supports_json_schema=True, supports_strict_schema=True,
            supports_usage_reporting=True, supports_request_id=True, supports_max_output_tokens=True)
        async def analyze(self, request):
            return AIProviderResponse(_payload(), self.name, self.model, self.model_version,
                100, 25, Decimal("0.00015"), provider_request_id="req-cert",
                provider_protocol=self.protocol, requested_output_mode="json_schema",
                effective_output_mode="json_schema", cost_status="PRICED",
                pricing_version="test-v1", usage_valid=True)

    provider = Provider()
    report = await AIProviderCertificationService(provider).certify(7)
    assert report["status"] == "PASSED" and report["checks"]["request_id"]
    assert AIControlRepository().certification(provider_identity(provider)["identity_checksum"])


@pytest.mark.asyncio
async def test_certification_failure_is_fail_closed(operations_db):
    from services.ai_operations import AIProviderCertificationService
    from services.ai_trading import AIProviderCapabilities, AIProviderError

    class Provider:
        name, protocol = "openai", "responses"
        endpoint, _api_key = "https://api.openai.com/v1/responses", "secret"
        model = model_version = "gpt-test"
        capabilities = AIProviderCapabilities(supports_json_schema=True, supports_strict_schema=True,
            supports_usage_reporting=True, supports_max_output_tokens=True)
        async def analyze(self, request):
            raise AIProviderError("AI_PROVIDER_HTTP_401")

    report = await AIProviderCertificationService(Provider()).certify()
    assert report["status"] == "FAILED" and report["failure_code"] == "AI_PROVIDER_HTTP_401"


@pytest.mark.asyncio
async def test_certification_timeout_is_bounded(operations_db, monkeypatch):
    from services.ai_operations import AIProviderCertificationService
    from services.ai_trading import AIProviderCapabilities
    monkeypatch.setenv("AI_REQUEST_TIMEOUT_SECONDS", "1")

    class Provider:
        name, protocol = "openai", "responses"
        endpoint, _api_key = "https://api.openai.com/v1/responses", "secret"
        model = model_version = "gpt-test"
        capabilities = AIProviderCapabilities(supports_json_schema=True, supports_strict_schema=True,
            supports_usage_reporting=True, supports_max_output_tokens=True)
        async def analyze(self, request):
            await asyncio.sleep(2)

    report = await AIProviderCertificationService(Provider()).certify()
    assert report["status"] == "FAILED" and report["failure_code"] == "CERTIFICATION_TIMEOUT"


def test_global_kill_switch_is_durable_and_immediate(operations_db):
    from services.ai_operations import AIControlRepository
    from services.ai_trading import AIProviderCapabilities, AITradingService
    controls = AIControlRepository()
    controls.set_kill(True, actor_telegram_id=7, reason_code="TEST")
    assert AIControlRepository().kill_status()["enabled"] == 1

    class Provider:
        name = model = model_version = "fake"
        protocol = "injected"
        capabilities = AIProviderCapabilities(supports_json_schema=True, supports_strict_schema=True)
    assert AITradingService(Provider())._activation_block() == "GLOBAL_AI_KILL_SWITCH"
    controls.set_kill(False, actor_telegram_id=7, reason_code="TEST_END")
    assert AITradingService(Provider())._activation_block() is None


@pytest.mark.asyncio
async def test_governance_state_enforces_mode_promotion(operations_db):
    from services.ai_operations import AIControlRepository, AIGovernanceState, AIProviderCertificationService, provider_identity
    from services.ai_trading import AIProviderResponse, AITradingMode, AITradingService, OpenAIResponsesAIProvider

    class Provider(OpenAIResponsesAIProvider):
        async def analyze(self, request):
            return AIProviderResponse(_payload(), self.name, self.model, self.model_version,
                10, 10, Decimal("0.00003"), provider_request_id="req-mode",
                provider_protocol=self.protocol, requested_output_mode="json_schema",
                effective_output_mode="json_schema", cost_status="PRICED",
                pricing_version="test-v1", usage_valid=True)

    provider = Provider()
    report = await AIProviderCertificationService(provider).certify()
    assert report["status"] == "PASSED"
    service = AITradingService(provider)
    assert service._activation_block(AITradingMode.AI_OBSERVE) is None
    assert service._activation_block(AITradingMode.AI_SHADOW) == "GOVERNANCE_MODE_NOT_CERTIFIED"
    identity = provider_identity(provider)
    AIControlRepository().transition(identity["provider"], identity["identity_checksum"],
                                     AIGovernanceState.SHADOW_CERTIFIED, "TEST_PROMOTION")
    assert service._activation_block(AITradingMode.AI_SHADOW) is None
    assert service._activation_block(AITradingMode.AI_ASSIST) == "GOVERNANCE_MODE_NOT_CERTIFIED"


def test_deterministic_experiment_assignment(operations_db):
    from database.database import connect
    from services.ai_operations import AIExperimentRepository
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("""INSERT INTO ai_experiments(experiment_key,name,status,variants_json,allocation_salt,
            started_at,created_at) VALUES(?,?,?,?,?,?,?)""",
            ("prompt-ab", "Prompt A/B", "RUNNING", '["A","B"]', "stable-salt", now, now))
    repository = AIExperimentRepository()
    assert repository.assignment("prompt-ab", 123) == repository.assignment("prompt-ab", 123)
    assert repository.assignment("missing", 123) is None


def test_rolling_metrics_and_drift_respect_minimum_samples(operations_db):
    from services.ai_evaluation import AIEvaluationService
    report = AIEvaluationService().rolling_metrics()
    assert set(report) == {"1h", "24h", "7d", "30d"}
    assert AIEvaluationService().drift(minimum_samples=1)["status"] == "INSUFFICIENT_SAMPLES"


def test_cost_reconciliation_never_treats_missing_invoice_as_zero(operations_db):
    from services.ai_operations import AICostReconciliationRepository
    report = AICostReconciliationRepository().record("openai", "2026-01-01", "2027-01-01", None)
    assert report["provider_cost_usd"] is None and report["status"] == "UNRECONCILED"

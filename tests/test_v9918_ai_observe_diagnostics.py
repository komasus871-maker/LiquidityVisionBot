from __future__ import annotations

from datetime import datetime, timedelta, timezone
from copy import deepcopy

import pytest


def _payload(*, quality: float = 37):
    return {
        "regime": "TREND",
        "direction": "NEUTRAL",
        "confidence": 35,
        "uncertainty": 65,
        "recommended_action": "ABSTAIN",
        "recommended_risk_multiplier": 0,
        "abstention": True,
        "supporting_factors": [
            {"evidence_id": "bounded_setup", "statement": "Bounded setup", "strength": 70},
        ],
        "conflicting_factors": [
            {"evidence_id": "uncertain_followthrough", "statement": "Uncertain follow-through",
             "severity": "HIGH"},
        ],
        "invalidation_conditions": ["No fresh confirmation"],
        "explanation": "The advisory response abstains under uncertainty.",
        "market_regimes": ["TREND"],
        "opportunity_quality": quality,
        "evidence_ranking": [{"evidence_id": "bounded_setup", "rank": 1}],
        "uncertainty_explanation": "Confirmation is incomplete.",
        "symbol": None,
        "reference_price": None,
    }


@pytest.fixture()
def observe_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "observe-diagnostics.db")
    monkeypatch.setenv("AI_TRADING_MODE", "AI_OBSERVE")
    monkeypatch.setenv("AI_CONTEXT_MAX_AGE_SECONDS", "300")
    monkeypatch.setenv("AI_MAX_DAILY_REQUESTS", "100")
    monkeypatch.setenv("AI_MAX_DAILY_REQUESTS_PER_USER", "100")
    monkeypatch.setenv("AI_MAX_DAILY_COST_USD", "10")
    monkeypatch.setenv("AI_PRICE_VERSION", "test")
    monkeypatch.setenv("AI_INPUT_COST_PER_MILLION_USD", "1")
    monkeypatch.setenv("AI_CACHE_WRITE_COST_PER_MILLION_USD", "1")
    monkeypatch.setenv("AI_OUTPUT_COST_PER_MILLION_USD", "1")
    monkeypatch.setenv("AI_CIRCUIT_BREAKER_FAILURES", "2")
    monkeypatch.setenv("AI_PROVIDER_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "1")
    from database.database import connect, create_tables

    create_tables()
    now = datetime.now(timezone.utc)
    with connect() as conn:
        for signal_id, symbol, updated_at in (
            (1801, "BTCUSDT", now - timedelta(minutes=10)),
            (1802, "ETHUSDT", now),
            (1803, "SOLUSDT", now - timedelta(minutes=10)),
        ):
            conn.execute("""INSERT INTO signals(id,owner_telegram_id,symbol,timeframe,side,status,
                created_at,updated_at,entry,stop,tp1,tp2,tp3,rr,confidence,bull_score,bear_score,
                recommendation,setup_key,features_json,reasons_json,current_price,max_profit_pct,
                max_drawdown_pct) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                signal_id, 18, symbol, "1h", "LONG", "ACTIVE",
                updated_at.isoformat(), updated_at.isoformat(), 100, 95, 110, 115, 120, 2,
                70, 70, 30, "READY", "diagnostic", "{}", "[]", 100, 0, 0,
            ))
    from services.ai_trading import AITradingService

    AITradingService._semaphore = None
    return connect


class Provider:
    name, protocol, endpoint = "injected", "responses", ""
    model = model_version = "synthetic-observe-model"

    def __init__(self, payload=None):
        from services.ai_trading import AIProviderCapabilities

        self.calls = 0
        self.payload = payload or _payload()
        self.capabilities = AIProviderCapabilities(
            supports_json_schema=True,
            supports_strict_schema=True,
            supports_max_output_tokens=True,
            supports_usage_reporting=True,
            supports_retryable_idempotent_requests=False,
        )

    async def analyze(self, request):
        from services.ai_trading import AIProviderResponse

        self.calls += 1
        return AIProviderResponse(
            self.payload,
            self.name,
            self.model,
            self.model_version,
            provider_protocol=self.protocol,
            requested_output_mode="json_schema",
            effective_output_mode="json_schema",
            extraction_path="responses.output[].message.content[].output_text",
            provider_completion_status="completed",
            provider_request_id=f"synthetic-{self.calls}",
            usage_valid=True,
        )


def test_certification_context_passes_while_stale_production_context_fails(observe_db):
    from services.ai_operations import AIProviderCertificationService
    from services.ai_trading import AIContextBuilder, AIResponseValidator

    validator = AIResponseValidator(max_age_seconds=300)
    certification = validator.validate(
        _payload(quality=0), AIProviderCertificationService._synthetic_context())
    production = validator.validate(
        _payload(), AIContextBuilder().from_signal(1801, telegram_id=18))

    assert certification.valid and certification.code == "VALID"
    assert not production.valid and production.code == "STALE_CONTEXT"
    assert production.validation_stage == "MARKET_TRUTH_VALIDATION"


@pytest.mark.asyncio
async def test_stale_context_is_rejected_before_provider_and_circuit_accounting(observe_db):
    from services.ai_intelligence import AIObservationIntelligence
    from services.ai_trading import AITradingService

    provider = Provider()
    row = await AITradingService(provider).analyze_signal(1801, telegram_id=18)
    health = AIObservationIntelligence().provider_health(row["provider_identity_checksum"])

    assert provider.calls == 0
    assert row["provider_invoked"] == 0
    assert row["validation_code"] == "STALE_CONTEXT"
    assert row["validation_stage"] == "MARKET_TRUTH_VALIDATION"
    assert row["opportunity_quality"] == 0
    assert health["validation_codes"] == {"STALE_CONTEXT": 1}
    assert health["observation_validation_failures"] == 1
    assert health["provider_response_validation_failures"] == 0
    assert health["circuit"] is None


@pytest.mark.asyncio
async def test_valid_abstain_is_semantically_valid_and_retains_quality(observe_db):
    from database.database import connect
    from services.ai_evaluation import AIEvaluationService
    from services.ai_trading import AITradingService

    provider = Provider(_payload(quality=37))
    row = await AITradingService(provider).analyze_signal(1802, telegram_id=18)
    metrics = AIEvaluationService().metrics(identity_checksum=row["provider_identity_checksum"])

    assert row["recommended_action"] == "ABSTAIN"
    assert row["validation_code"] == "VALID"
    assert row["opportunity_quality"] == 37
    assert metrics["semantic_valid_count"] == 1
    assert metrics["semantic_valid_rate"] == 1
    assert metrics["abstention_rate"] == 1
    with connect() as conn:
        event = conn.execute(
            "SELECT * FROM ai_provider_request_events ORDER BY id DESC LIMIT 1").fetchone()
    assert event["status"] == "COMPLETED"
    assert event["reason_code"] == "VALID"
    assert event["validation_stage"] == "COMPLETE"
    assert event["validation_code"] == "VALID"
    assert event["extraction_code"] == "VALID"
    assert event["schema_valid"] == 1 and event["semantic_valid"] == 1


@pytest.mark.asyncio
async def test_provider_semantic_contract_failure_still_counts_against_circuit(observe_db):
    from database.database import connect
    from services.ai_trading import AITradingService

    invalid = deepcopy(_payload())
    invalid["evidence_ranking"] = [
        {"evidence_id": "uncertain_followthrough", "rank": 1},
    ]
    provider = Provider(invalid)
    row = await AITradingService(provider).analyze_signal(1802, telegram_id=18)

    assert row["validation_code"] == "EVIDENCE_RANK_CONFLICTING_REFERENCE"
    with connect() as conn:
        state = conn.execute("SELECT * FROM ai_provider_state ORDER BY updated_at DESC LIMIT 1").fetchone()
        event = conn.execute(
            "SELECT * FROM ai_provider_request_events ORDER BY id DESC LIMIT 1").fetchone()
    assert state["consecutive_failures"] == 1 and state["total_failures"] == 1
    assert event["status"] == "COMPLETED"
    assert event["reason_code"] == "EVIDENCE_RANK_CONFLICTING_REFERENCE"
    assert event["validation_stage"] == "SEMANTIC_VALIDATION"
    assert event["semantic_valid"] == 0


@pytest.mark.asyncio
async def test_queue_distinguishes_exceptions_from_validation_rejections(observe_db):
    from services.ai_trading import AIShadowWorker, AITradingService

    provider = Provider()
    worker = AIShadowWorker()
    AITradingService._semaphore = None
    worker.service = AITradingService(provider)
    result = await worker.check_once()

    assert result["processed"] == 3
    assert result["failed"] == 0
    assert result["validation_failed"] == 2
    assert provider.calls == 1
    with observe_db() as conn:
        queue = conn.execute(
            "SELECT * FROM ai_observation_queue_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    assert queue["failed"] == 0 and queue["validation_failed"] == 2


def test_schema_rate_signature_and_persisted_flag_are_consistent(observe_db):
    from database.database import connect
    from services.ai_evaluation import AIEvaluationService
    from services.ai_intelligence import AIObservationIntelligence
    from services.ai_trading import (AIDecisionRepository, AIContext, AIProviderResponse,
                                     AIResponseValidator, AITradingMode)

    repository = AIDecisionRepository()
    identity = "production-signature"
    for index in range(18):
        context = AIContext(
            telegram_id=18,
            signal_id=1900 + index,
            symbol="BTCUSDT",
            timeframe="1h",
            market_timestamp=datetime.now(timezone.utc).isoformat(),
            market={"price": 100},
            features={},
            portfolio={},
            history={},
            deterministic={"direction": "LONG"},
            market_checksum=f"market-{index}",
            feature_checksum=f"features-{index}",
        )
        if index < 14:
            decision = AIResponseValidator().fallback("STALE_CONTEXT", "MARKET_TRUTH_VALIDATION")
        else:
            decision = AIResponseValidator().fallback("PROVIDER_TIMEOUT", "PROVIDER_TRANSPORT")
        response = AIProviderResponse(
            None,
            "openai",
            "gpt-5.6-terra",
            "gpt-5.6-terra",
            identity_checksum=identity,
            provider_invoked=True,
        )
        repository.save(
            context=context,
            response=response,
            decision=decision,
            mode=AITradingMode.AI_OBSERVE,
            latency_ms=100 if index < 14 else 45_331,
        )

    metrics = AIEvaluationService().metrics(identity_checksum=identity)
    with connect() as conn:
        stored_schema_valid = conn.execute(
            "SELECT COALESCE(SUM(schema_valid),0) n FROM ai_decisions "
            "WHERE provider_identity_checksum=?", (identity,)).fetchone()["n"]
        conn.execute("""INSERT INTO ai_provider_state(provider,state,consecutive_failures,
            opened_until,last_failure_at,last_error_code,identity_checksum,total_requests,
            total_failures,total_retries,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
            f"openai:{identity}", "OPEN", 18,
            (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            datetime.now(timezone.utc).isoformat(), "PROVIDER_TIMEOUT", identity, 18, 18, 0,
            datetime.now(timezone.utc).isoformat(),
        ))
    for index in range(18):
        failed = index >= 14
        AIObservationIntelligence.record_request_event(
            identity_checksum=identity,
            signal_id=1900 + index,
            attempt=1,
            status="FAILED" if failed else "COMPLETED",
            reason_code="PROVIDER_TIMEOUT" if failed else "VALID",
            latency_ms=45_331 if failed else 100,
        )
    health = AIObservationIntelligence().provider_health(identity)

    assert metrics["decision_count"] == 18
    assert metrics["schema_valid_count"] == 14
    assert metrics["valid_schema_rate"] == pytest.approx(14 / 18)
    assert metrics["semantic_valid_rate"] == 0
    assert metrics["abstention_rate"] == 1
    assert metrics["p95_latency_ms"] == 45_331
    assert metrics["schema_evaluable_count"] == 14
    assert metrics["schema_not_evaluable_count"] == 4
    assert metrics["structural_schema_valid_count"] == 14
    assert metrics["structural_schema_invalid_count"] == 0
    assert metrics["structural_schema_valid_rate"] == 1
    assert stored_schema_valid == metrics["schema_valid_count"]
    assert health["circuit"]["state"] == "OPEN"
    assert health["circuit"]["total_failures"] == 18
    assert health["recent_events"] == 18 and health["transport_failures"] == 4
    assert health["classified_provider_failures"] == 4
    assert health["counter_classification_drift"] == 14
    assert health["validation_codes"] == {"PROVIDER_TIMEOUT": 4, "STALE_CONTEXT": 14}

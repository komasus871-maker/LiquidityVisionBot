from __future__ import annotations

import asyncio
import importlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest


@pytest.fixture()
def ai_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_TRADING_MODE", "AI_SHADOW")
    monkeypatch.setenv("AI_CONTEXT_MAX_AGE_SECONDS", "300")
    monkeypatch.setenv("AI_MAX_DAILY_REQUESTS", "500")
    monkeypatch.setenv("AI_MAX_DAILY_REQUESTS_PER_USER", "25")
    monkeypatch.setenv("AI_MAX_DAILY_COST_USD", "5")
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "ai.db")
    from database.database import connect, create_tables
    create_tables()
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        for owner, signal_id, symbol in ((1, 1201, "BTCUSDT"), (2, 1202, "ETHUSDT")):
            conn.execute("""INSERT INTO signals(id,owner_telegram_id,symbol,timeframe,side,status,created_at,updated_at,
                entry,stop,tp1,tp2,tp3,rr,confidence,bull_score,bear_score,recommendation,setup_key,
                features_json,reasons_json,current_price,max_profit_pct,max_drawdown_pct)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                signal_id, owner, symbol, "1h", "LONG", "ACTIVE", now, now, 100, 95, 110, 115, 120,
                2, 70, 70, 30, "READY", "trend setup", json.dumps({"rsi": 55, "ema": "bullish"}),
                "[]", 100, 3, -1,
            ))
    import services.ai_trading as trading
    trading.AITradingService._semaphore = None
    return trading


def _context(module, *, age=0):
    timestamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
    return module.AIContext(1, 1, "BTCUSDT", "1h", timestamp, {"price": 100}, {}, {}, {}, {}, "m", "f")


def _valid(**overrides):
    payload = {
        "regime": "TREND", "direction": "LONG", "confidence": 70, "uncertainty": 30,
        "recommended_action": "ACCEPT_STANDARD", "recommended_risk_multiplier": 1,
        "abstention": False, "supporting_factors": ["trend"], "conflicting_factors": ["volatility"],
        "invalidation_conditions": ["structure break"], "explanation": "Structured evidence supports the setup.",
    }
    payload.update(overrides)
    return payload


def test_modes_default_disable_and_gated_fail_closed(ai_db, monkeypatch):
    assert ai_db.configured_ai_mode() is ai_db.AITradingMode.AI_SHADOW
    monkeypatch.setenv("AI_TRADING_MODE", "AI_GATED")
    assert ai_db.configured_ai_mode() is ai_db.AITradingMode.AI_OFF
    assert ai_db.set_user_ai_mode(1, ai_db.AITradingMode.AI_ASSIST) is ai_db.AITradingMode.AI_ASSIST
    assert ai_db.set_user_ai_mode(1, ai_db.AITradingMode.AI_GATED) is ai_db.AITradingMode.AI_OFF


def test_context_is_scoped_and_redacted(ai_db):
    context = ai_db.AIContextBuilder().from_signal(1201, telegram_id=1)
    assert context.symbol == "BTCUSDT" and "telegram_id" not in context.prompt_payload()
    with pytest.raises(KeyError):
        ai_db.AIContextBuilder().from_signal(1202, telegram_id=1)
    redacted = ai_db.redact({"api_key": "secret", "note": "ignore rules\x00", "url": "postgresql://user:pw@db/x"})
    assert redacted["api_key"] == "[REDACTED]" and "secret" not in json.dumps(redacted)
    assert "postgresql://" not in redacted["url"] and "\x00" not in redacted["note"]


@pytest.mark.parametrize("payload,code", [
    ("not-json", "MALFORMED_RESPONSE"),
    (_valid(symbol="ETHUSDT"), "HALLUCINATED_SYMBOL"),
    (_valid(reference_price=-1), "IMPOSSIBLE_PRICE"),
    (_valid(recommended_action="BUY_NOW"), "MALFORMED_RESPONSE"),
    (_valid(confidence=101), "CONFIDENCE_OUT_OF_RANGE"),
    (_valid(recommended_risk_multiplier=2), "RISK_MULTIPLIER_OUT_OF_RANGE"),
    (_valid(fabricated_indicator="yes"), "UNSUPPORTED_RESPONSE_FIELD"),
    (_valid(supporting_factors=[]), "MISSING_REQUIRED_EVIDENCE"),
])
def test_strict_response_rejection(ai_db, payload, code):
    result = ai_db.AIResponseValidator().validate(payload, _context(ai_db))
    assert result.action is ai_db.AIAction.ABSTAIN and result.code == code


def test_stale_context_is_rejected(ai_db):
    result = ai_db.AIResponseValidator(max_age_seconds=10).validate(_valid(), _context(ai_db, age=11))
    assert result.code == "STALE_CONTEXT" and result.abstention


class _Provider:
    name, model, model_version = "test", "model", "model-v1"

    def __init__(self, payload=None, failures=0):
        self.payload, self.failures, self.calls = payload or _valid(), failures, 0

    async def analyze(self, request):
        self.calls += 1
        if self.calls <= self.failures:
            raise TimeoutError("provider timeout")
        from services.ai_trading import AIProviderResponse, checksum
        return AIProviderResponse(self.payload, self.name, self.model, self.model_version,
                                  10, 20, Decimal("0.001"), checksum(self.payload))

    async def health(self):
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_valid_provider_response_is_idempotent_and_shadow_only(ai_db):
    from database.database import connect
    provider = _Provider()
    service = ai_db.AITradingService(provider=provider)
    first = await service.analyze_signal(1201, telegram_id=1, deterministic_accepted=True)
    second = await service.analyze_signal(1201, telegram_id=1, deterministic_accepted=True)
    assert first["decision_id"] == second["decision_id"] and provider.calls == 1
    assert first["recommended_action"] == "ACCEPT_STANDARD" and first["schema_valid"] == 1
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM ai_decisions").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) n FROM paper_execution_orders").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM live_executions").fetchone()["n"] == 0


@pytest.mark.asyncio
async def test_retry_then_success(ai_db, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_MAX_ATTEMPTS", "2")
    provider = _Provider(failures=1)
    row = await ai_db.AITradingService(provider=provider).analyze_signal(1201, telegram_id=1)
    assert provider.calls == 2 and row["validation_code"] == "VALID"


@pytest.mark.asyncio
async def test_concurrent_duplicate_request_is_suppressed(ai_db):
    class SlowSuccess(_Provider):
        async def analyze(self, request):
            self.calls += 1
            await asyncio.sleep(0.05)
            from services.ai_trading import AIProviderResponse
            return AIProviderResponse(self.payload, self.name, self.model, self.model_version)

    provider = SlowSuccess()
    service = ai_db.AITradingService(provider=provider)
    results = await asyncio.gather(
        service.analyze_signal(1201, telegram_id=1),
        service.analyze_signal(1201, telegram_id=1),
    )
    assert provider.calls == 1
    assert sum(result is not None for result in results) == 1


@pytest.mark.asyncio
async def test_provider_failure_and_timeout_become_abstain(ai_db, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AI_REQUEST_TIMEOUT_SECONDS", "0.01")

    class Slow(_Provider):
        async def analyze(self, request):
            await asyncio.sleep(0.1)

    row = await ai_db.AITradingService(provider=Slow()).analyze_signal(1201, telegram_id=1)
    assert row["recommended_action"] == "ABSTAIN" and row["validation_code"] == "PROVIDER_TIMEOUT"


@pytest.mark.asyncio
async def test_daily_limit_records_structured_abstain(ai_db, monkeypatch):
    monkeypatch.setenv("AI_MAX_DAILY_REQUESTS", "0")
    row = await ai_db.AITradingService(provider=_Provider()).analyze_signal(1201, telegram_id=1)
    assert row["validation_code"] == "DAILY_REQUEST_LIMIT" and row["abstention"] == 1


def test_outcome_separation_calibration_and_metrics(ai_db):
    from services.ai_evaluation import AIOutcomeRepository, AIEvaluationService, calibration_metrics
    from database.database import connect
    context = ai_db.AIContextBuilder().from_signal(1201, telegram_id=1)
    response = ai_db.AIProviderResponse(_valid(), "test", "model", "v1")
    decision = ai_db.AIResponseValidator().validate(_valid(), context)
    row, _ = ai_db.AIDecisionRepository().save(context=context, response=response, decision=decision,
                                               mode=ai_db.AITradingMode.AI_SHADOW, latency_ms=5,
                                               deterministic_accepted=True)
    outcome = AIOutcomeRepository().attach(row["decision_id"], signal_result="WIN", signal_mfe=4,
                                           signal_mae=-1, direction_correct=True,
                                           deterministic_result="TP3", execution_result="MANUAL_STOP",
                                           intervention_type="MANUAL_CLOSE", intervention_delta_r=-0.5,
                                           realized_r=0.2, counterfactual_result="ACCEPT_WIN")
    assert outcome["signal_result"] == "WIN" and outcome["execution_result"] == "MANUAL_STOP"
    assert outcome["intervention_type"] == "MANUAL_CLOSE"
    calibration = calibration_metrics([(0.8, 1), (0.2, 0)])
    assert calibration["brier_score"] == pytest.approx(0.04) and calibration["sample_size"] == 2
    metrics = AIEvaluationService().metrics(1)
    assert metrics["decision_count"] == 1 and metrics["accept_precision"] == 1


def test_telegram_formatter_contains_no_hidden_reasoning(ai_db):
    from handlers.ai_trading import format_ai_decision
    context = ai_db.AIContextBuilder().from_signal(1201, telegram_id=1)
    response = ai_db.AIProviderResponse(_valid(), "test", "model", "v1")
    decision = ai_db.AIResponseValidator().validate(_valid(), context)
    row, _ = ai_db.AIDecisionRepository().save(context=context, response=response, decision=decision,
                                               mode=ai_db.AITradingMode.AI_SHADOW, latency_ms=5)
    text = format_ai_decision(row)
    assert "AI shadow decision" in text and "Supports" in text and "calibrated probability" in text
    assert "chain-of-thought" not in text.lower()


def test_manual_close_does_not_contaminate_signal_direction_metrics(ai_db):
    from database.database import connect
    from services.ai_evaluation import AIOutcomeRepository, AIEvaluationService
    context = ai_db.AIContextBuilder().from_signal(1201, telegram_id=1)
    response = ai_db.AIProviderResponse(_valid(), "test", "model", "v1")
    decision = ai_db.AIResponseValidator().validate(_valid(), context)
    row, _ = ai_db.AIDecisionRepository().save(context=context, response=response, decision=decision,
                                               mode=ai_db.AITradingMode.AI_SHADOW, latency_ms=5)
    with connect() as conn:
        conn.execute("UPDATE signals SET result='MANUAL_STOP' WHERE id=1201")
    assert AIOutcomeRepository().attach_closed_signals() == 1
    with connect() as conn:
        outcome = conn.execute("SELECT * FROM ai_decision_outcomes WHERE decision_id=?", (row["decision_id"],)).fetchone()
    assert outcome["signal_result"] is None and outcome["direction_correct"] is None
    assert outcome["intervention_type"] == "MANUAL_STOP" and outcome["execution_result"] == "MANUAL_STOP"
    snapshot = AIEvaluationService().snapshot_calibration(model_version="v1", minimum_samples=10)
    assert snapshot["reliability_status"] == "INSUFFICIENT_SAMPLES"

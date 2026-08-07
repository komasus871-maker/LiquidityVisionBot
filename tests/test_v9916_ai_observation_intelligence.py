from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


def _payload(action="ACCEPT_STANDARD"):
    accepted = action == "ACCEPT_STANDARD"
    return {
        "regime": "trend breakout", "direction": "LONG", "confidence": 78,
        "uncertainty": 22, "recommended_action": action,
        "recommended_risk_multiplier": 1 if accepted else 0,
        "abstention": action == "ABSTAIN", "supporting_factors": ["bullish structure"],
        "conflicting_factors": ["elevated volatility"],
        "invalidation_conditions": ["structure break"],
        "explanation": "Bullish structure supports an advisory counterfactual.",
        "market_regimes": ["TREND", "BREAKOUT"], "opportunity_quality": 82,
        "evidence_ranking": ["bullish structure"],
        "uncertainty_explanation": "Volatility may invalidate the breakout.",
        "symbol": "BTCUSDT", "reference_price": 100,
    }


@pytest.fixture()
def observation_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "ai-observation.db")
    monkeypatch.setenv("AI_TRADING_MODE", "AI_OBSERVE")
    monkeypatch.setenv("AI_MAX_DAILY_REQUESTS", "100")
    monkeypatch.setenv("AI_MAX_DAILY_REQUESTS_PER_USER", "100")
    monkeypatch.setenv("AI_MAX_DAILY_COST_USD", "10")
    monkeypatch.setenv("AI_PRICE_VERSION", "test")
    monkeypatch.setenv("AI_INPUT_COST_PER_MILLION_USD", "1")
    monkeypatch.setenv("AI_CACHE_WRITE_COST_PER_MILLION_USD", "1")
    monkeypatch.setenv("AI_OUTPUT_COST_PER_MILLION_USD", "1")
    monkeypatch.setenv("AI_OBSERVATION_CACHE_ENABLED", "true")
    monkeypatch.setenv("AI_OBSERVATION_CACHE_TTL_SECONDS", "300")
    from database.database import connect, create_tables
    create_tables()
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        for signal_id, timeframe in ((1601, "5m"), (1602, "15m")):
            conn.execute("""INSERT INTO signals(id,owner_telegram_id,symbol,timeframe,side,status,
                created_at,updated_at,entry,stop,tp1,tp2,tp3,rr,confidence,bull_score,bear_score,
                recommendation,setup_key,features_json,reasons_json,current_price,max_profit_pct,
                max_drawdown_pct) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                signal_id, 11, "BTCUSDT", timeframe, "LONG", "ACTIVE", now, now, 100, 95,
                110, 115, 120, 2, 78, 78, 22, "READY", "breakout",
                '{"bos":true,"atr":2,"volatility":"high"}', "[]", 100, 0, 0))
    from services.research_engine import ResearchEngine
    research = ResearchEngine()
    research.capture_signal(1601)
    research.capture_signal(1602)
    from services.ai_trading import AITradingService
    AITradingService._semaphore = None
    return connect


class Provider:
    name, protocol, endpoint = "injected", "responses", ""
    model = model_version = "gpt-5.6-terra-test"

    def __init__(self):
        from services.ai_trading import AIProviderCapabilities
        self.calls = 0
        self.capabilities = AIProviderCapabilities(
            supports_json_schema=True, supports_strict_schema=True,
            supports_max_output_tokens=True, supports_usage_reporting=True,
            supports_retryable_idempotent_requests=True)

    async def analyze(self, request):
        from services.ai_trading import AIProviderResponse
        self.calls += 1
        return AIProviderResponse(_payload(), self.name, self.model, self.model_version,
                                  provider_protocol=self.protocol,
                                  requested_output_mode="json_schema",
                                  effective_output_mode="json_schema", usage_valid=True)


@pytest.mark.asyncio
async def test_materially_unchanged_signal_reuses_observation_without_provider_call(observation_db):
    from services.ai_trading import AITradingService
    provider = Provider()
    service = AITradingService(provider)
    first = await service.analyze_signal(1601, telegram_id=11)
    with observation_db() as conn:
        conn.execute("UPDATE signals SET updated_at=? WHERE id=1601",
                     ((datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),))
    second = await service.analyze_signal(1601, telegram_id=11)
    assert provider.calls == 1 and first["decision_id"] != second["decision_id"]
    assert second["cache_hit"] == 1 and second["cache_source_decision_id"] == first["decision_id"]
    assert second["provider_invoked"] == 0 and second["estimated_cost_usd"] == 0
    with observation_db() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM paper_execution_orders").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM live_executions").fetchone()["n"] == 0


@pytest.mark.asyncio
async def test_shadow_intelligence_persists_similarity_regimes_and_closed_evaluation(observation_db):
    from services.ai_intelligence import AIObservationIntelligence
    from services.ai_trading import AITradingService
    provider = Provider()
    service = AITradingService(provider)
    first = await service.analyze_signal(1601, telegram_id=11)
    second = await service.analyze_signal(1602, telegram_id=11)
    with observation_db() as conn:
        intelligence = conn.execute("SELECT * FROM ai_decision_intelligence WHERE decision_id=?",
                                    (second["decision_id"],)).fetchone()
        similarity = conn.execute("SELECT * FROM ai_decision_similarities WHERE source_decision_id=?",
                                  (second["decision_id"],)).fetchone()
        conn.execute("UPDATE signals SET status='TP3',result='TP3',realized_r=3,closed_at=? WHERE id IN (1601,1602)",
                     (datetime.now(timezone.utc).isoformat(),))
    assert intelligence and "BREAKOUT" in intelligence["market_regimes_json"]
    assert similarity and similarity["similar_signal_id"] == 1601
    engine = AIObservationIntelligence()
    assert engine.evaluate_closed() == 2
    report = engine.counterfactual_report()
    regimes = engine.regime_report()["regimes"]
    learning = engine.refresh_learning()
    assert report["true_positives"] == 2 and report["gpt_expectancy_r"] == 3
    assert regimes["TREND"]["samples"] == 2 and regimes["BREAKOUT"]["samples"] == 2
    assert learning["sample_size"] == 2 and learning["snapshot_key"]
    assert first["deterministic_accepted"] == 1


@pytest.mark.asyncio
async def test_cancelled_provider_request_is_audited_and_releases_claim(observation_db):
    from services.ai_trading import AITradingService

    class Slow(Provider):
        async def analyze(self, request):
            self.calls += 1
            await asyncio.sleep(10)

    service = AITradingService(Slow())
    task = asyncio.create_task(service.analyze_signal(1601, telegram_id=11))
    await asyncio.sleep(.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with observation_db() as conn:
        event = conn.execute("SELECT * FROM ai_provider_request_events ORDER BY id DESC LIMIT 1").fetchone()
        claims = conn.execute("SELECT COUNT(*) n FROM ai_request_claims").fetchone()["n"]
    assert event["status"] == "CANCELLED" and event["reason_code"] == "REQUEST_CANCELLED"
    assert claims == 0


@pytest.mark.asyncio
async def test_intervened_ai_outcome_is_persisted_but_excluded_from_learning(observation_db):
    from services.ai_evaluation import AIOutcomeRepository
    from services.ai_intelligence import AIObservationIntelligence
    from services.ai_trading import AITradingService
    provider = Provider()
    decision = await AITradingService(provider).analyze_signal(1601, telegram_id=11)
    with observation_db() as conn:
        conn.execute("UPDATE signals SET status='MANUAL_STOP',result='MANUAL_STOP',realized_r=-.2,closed_at=? WHERE id=1601",
                     (datetime.now(timezone.utc).isoformat(),))
    assert AIOutcomeRepository().attach_closed_signals() == 1
    engine = AIObservationIntelligence()
    assert engine.evaluate_closed() == 1
    with observation_db() as conn:
        row = conn.execute("SELECT * FROM ai_counterfactual_evaluations WHERE decision_id=?",
                           (decision["decision_id"],)).fetchone()
    assert row["intervention_type"] == "MANUAL_STOP" and row["evaluation_eligible"] == 0
    assert engine.counterfactual_report(telegram_id=11)["sample_size"] == 0


def test_ai_startup_validation_uses_canonical_sqlite_backend(observation_db):
    from services.ai_intelligence import AIObservationIntelligence

    result = AIObservationIntelligence.startup_validate()

    assert result["valid"] is True
    assert result["missing_tables"] == []


def test_ai_startup_validation_uses_canonical_postgresql_backend(monkeypatch):
    from services import ai_intelligence

    required = {
        "ai_decisions", "ai_decision_intelligence", "ai_counterfactual_evaluations",
        "ai_learning_snapshots", "ai_provider_request_events",
    }

    class Cursor:
        def __init__(self, rows=(), rowcount=0):
            self._rows = list(rows)
            self.rowcount = rowcount

        def fetchall(self):
            return self._rows

    class Connection:
        def __init__(self):
            self.queries = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            self.queries.append(sql)
            if "information_schema.tables" in sql:
                return Cursor(({"table_name": name} for name in required))
            return Cursor(rowcount=0)

    connection = Connection()
    monkeypatch.setattr(ai_intelligence, "database_backend", lambda: "postgresql")
    monkeypatch.setattr(ai_intelligence, "connect", lambda: connection)

    result = ai_intelligence.AIObservationIntelligence.startup_validate()

    assert result["valid"] is True
    assert result["missing_tables"] == []
    assert any("information_schema.tables" in query for query in connection.queries)
    assert all("sqlite_master" not in query for query in connection.queries)


@pytest.mark.parametrize("backend", [None, "unknown"])
def test_ai_startup_validation_fails_closed_for_unknown_backend(monkeypatch, backend):
    from services import ai_intelligence

    monkeypatch.setattr(ai_intelligence, "database_backend", lambda: backend)
    monkeypatch.setattr(ai_intelligence, "connect",
                        lambda: pytest.fail("database must not be queried for an unknown backend"))

    result = ai_intelligence.AIObservationIntelligence.startup_validate()

    assert result["valid"] is False
    assert result["failure_reason"] == "DATABASE_BACKEND_UNDETERMINED"
    assert result["ai_gated_execution_authority"] is False


def test_ai_startup_validation_fails_closed_when_backend_detection_raises(monkeypatch):
    from services import ai_intelligence

    def unavailable():
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(ai_intelligence, "database_backend", unavailable)
    monkeypatch.setattr(ai_intelligence, "connect",
                        lambda: pytest.fail("database must not be queried when detection fails"))

    result = ai_intelligence.AIObservationIntelligence.startup_validate()

    assert result["valid"] is False
    assert result["failure_reason"] == "DATABASE_BACKEND_UNDETERMINED"
    assert result["failure_detail"] == "RuntimeError"


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_mode", ["polling", "webhook"])
async def test_bot_startup_reaches_telegram_transport_after_ai_validation(
        observation_db, monkeypatch, transport_mode):
    import importlib
    from types import SimpleNamespace

    monkeypatch.setenv("BOT_TOKEN", "123456:startup-regression-token")
    bot_module = importlib.import_module("bot")
    reached = {"startup": False, "polling": False, "webhook": False, "shutdown": False}

    class TransportReached(Exception):
        pass

    class FakeBot:
        def __init__(self, *args, **kwargs):
            self.session = SimpleNamespace(close=self.close)

        async def delete_webhook(self, **kwargs):
            return True

        async def close(self):
            return None

    class FakeDispatcher:
        async def emit_startup(self, **kwargs):
            reached["startup"] = True

        async def start_polling(self, *args, **kwargs):
            reached["polling"] = True

        async def emit_shutdown(self, **kwargs):
            reached["shutdown"] = True

        def resolve_used_update_types(self):
            return []

    class Worker:
        def __init__(self, *args, **kwargs):
            pass

        async def run_forever(self):
            await asyncio.Event().wait()

        def stop(self):
            pass

    class FakeWebhookServer:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            reached["webhook"] = True
            raise TransportReached

        async def stop(self):
            return None

    class Migration:
        def run(self, **kwargs):
            return SimpleNamespace(as_dict=lambda: {})

    class Memory:
        def backfill(self, **kwargs):
            return {"scanned": 0, "created": 0}

    class ConfigValidator:
        def validate(self):
            return SimpleNamespace(valid=True, errors=[], warnings=[])

    monkeypatch.setattr(bot_module, "create_tables", lambda: None)
    monkeypatch.setattr(bot_module, "HistoricalExecutionMigrationService", Migration)
    monkeypatch.setattr(bot_module, "TradeMemoryService", Memory)
    monkeypatch.setattr(bot_module, "ping_database", lambda: {"latency_ms": 0})
    monkeypatch.setattr(bot_module, "database_backend", lambda: "sqlite")
    monkeypatch.setattr(bot_module, "persistent_database", lambda: False)
    monkeypatch.setattr(bot_module, "AIConfigurationValidator", ConfigValidator)
    monkeypatch.setattr(bot_module, "Bot", FakeBot)
    monkeypatch.setattr(bot_module, "build_dispatcher", FakeDispatcher)
    monkeypatch.setattr(bot_module, "deployment_mode", lambda: transport_mode)
    monkeypatch.setattr(bot_module, "WebhookServer", FakeWebhookServer)
    for name in ("SignalTracker", "ObservationMonitor", "WatchEngine", "CopyExecutionWorker",
                 "AIShadowWorker", "ResearchWorker"):
        monkeypatch.setattr(bot_module, name, Worker)

    if transport_mode == "webhook":
        with pytest.raises(TransportReached):
            await bot_module.main()
    else:
        await bot_module.main()

    assert reached["startup"] is True
    assert reached[transport_mode] is True
    assert reached["shutdown"] is True

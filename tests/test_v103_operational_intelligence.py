from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography.fernet import Fernet


@pytest.fixture()
def v103_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "v103.db")
    from database.database import create_tables
    create_tables()
    return tmp_path / "v103.db"


def test_empty_database_collector_uses_bounded_seed_universe(v103_db, monkeypatch):
    monkeypatch.setenv("MICROSTRUCTURE_COLLECTION_ENABLED", "true")
    monkeypatch.setenv("MICROSTRUCTURE_SYMBOLS", "BTCUSDT,ETHUSDT")
    monkeypatch.setenv("MICROSTRUCTURE_MAX_SYMBOLS", "1")
    from services.microstructure_observer import MicrostructureObserver

    worker = MicrostructureObserver()
    assert worker._symbols() == ["BTCUSDT"]
    health = worker.repository.worker_health()
    assert health["configured_enabled"] == 1
    assert health["active_symbols"] == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_partial_public_sources_persist_independently(v103_db, monkeypatch):
    import services.microstructure_observer as observer_module
    from database.database import connect

    class PartialAdapter:
        environment = "prod-live"

        async def market_depth(self, symbol, limit):
            raise RuntimeError("DEPTH_OFFLINE")

        async def funding_snapshot(self, symbol):
            return {"funding_rate": "0.0002", "mark_price": "100"}

        async def open_interest_snapshot(self, symbol):
            raise RuntimeError("OI_OFFLINE")

        async def close(self):
            return None

    monkeypatch.setattr(observer_module, "_public_bingx_adapter", PartialAdapter)
    monkeypatch.setenv("MICROSTRUCTURE_COLLECTION_ENABLED", "true")
    monkeypatch.setenv("MICROSTRUCTURE_SYMBOLS", "BTCUSDT")
    result = await observer_module.MicrostructureObserver(interval_seconds=30).check_once()

    assert result["state"] == "DEGRADED"
    assert result["persisted"] == 0 and result["source_persisted"] == 1
    assert result["source_health"]["FUNDING"]["succeeded"] == 1
    assert result["source_health"]["DEPTH"]["failed"] == 1
    assert result["source_health"]["OPEN_INTEREST"]["failed"] == 1
    with connect() as conn:
        rows = conn.execute("SELECT source_type FROM market_source_snapshots").fetchall()
    assert [row["source_type"] for row in rows] == ["FUNDING"]


def test_derivatives_history_is_bounded_and_has_no_future_leakage(v103_db):
    from services.market_intelligence_repository import MarketIntelligenceRepository

    repo = MarketIntelligenceRepository()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(21):
        observed = (base + timedelta(minutes=index)).isoformat()
        repo.persist_source_snapshot(
            symbol="BTCUSDT", exchange="bingx", environment="prod-live",
            source_type="FUNDING", provider="TEST",
            snapshot={"funding_rate": index / 100_000}, observed_at=observed)
    cutoff = (base + timedelta(minutes=18)).isoformat()
    history = repo.source_history("BTCUSDT", "FUNDING", as_of=cutoff, limit=500)
    assert history["history_points"] == 19
    assert history["percentile"] is None
    assert history["observations"][-1]["observed_at"] == cutoff
    full = repo.source_history("BTCUSDT", "FUNDING", as_of=(base + timedelta(minutes=25)).isoformat())
    assert full["history_points"] == 21 and full["percentile"] == 100
    assert full["future_data_used"] is False


def test_quality_readiness_scanner_and_fusion_v3_semantics():
    from services.market_intelligence import MarketIntelligenceEngine
    from services.scanner import Scanner

    quality = {
        "invalidation": {"valid_geometry": True}, "data_confidence": 70,
        "setup_quality": "LOW",
        "family_scores": {"LOCATION": 70, "MOMENTUM": 80, "MICROSTRUCTURE": None,
                          "STRUCTURE": 80, "INVALIDATION": 80,
                          "TARGET_REALISM": 75, "EXECUTION_COST": 70},
        "contradicting_evidence": [
            {"family": "STRUCTURE", "severity": "HIGH", "reason": "opposes direction"}],
    }
    readiness = MarketIntelligenceEngine._entry_readiness(
        plan={"entry": 100, "stop": 95, "rr": 2}, quality=quality,
        microstructure={"status": "UNAVAILABLE"}, momentum={"state": "STRONG"},
        structure={"break": "CLOSE_CONFIRMED_BREAK"}, data_quality={"status": "GOOD"})
    assert readiness["version"] == "entry-readiness-v3"
    assert readiness["state"] == "WAIT_STRUCTURE" and readiness["score"] <= 64
    assert readiness["components"]["MICROSTRUCTURE"] is None
    assert readiness["data_confidence"] == 70

    priority = Scanner._priority_score(
        quality=35, readiness=79, strategy_fit=50, data_confidence=55, ev_rank=100,
        contradictions=quality["contradicting_evidence"], readiness_state="WAIT_STRUCTURE")
    assert priority < 65
    exceptional = Scanner._priority_score(
        quality=96, readiness=94, strategy_fit=93, data_confidence=92, ev_rank=98,
        contradictions=[], readiness_state="READY")
    assert exceptional >= 93
    assert Scanner._priority_score(quality=None, readiness=90, strategy_fit=90,
                                   data_confidence=90, ev_rank=90,
                                   contradictions=[], readiness_state="READY") is None

    fusion = MarketIntelligenceEngine._strategy_fusion({"BREAKOUT": 60, "LIQUIDITY_SMC": 60})
    assert fusion["fusion_state"] == "TIE"
    assert fusion["primary"]["strategy"] is None
    assert fusion["tied_strategies"] == ["BREAKOUT", "LIQUIDITY_SMC"]


def test_help_classification_discoverability_menu_and_localization():
    import re
    from services.command_catalog import (ALL_DOCUMENTED_COMMANDS, COMMAND_CLASSIFICATION,
                                          HELP_CATALOG, MAIN_MENU_COMMANDS, OPERATOR_COMMANDS,
                                          PUBLIC_COMMANDS)
    from services.localization import LocalizationService

    registered = set()
    for path in (Path(__file__).parents[1] / "handlers").glob("*.py"):
        for arguments in re.findall(r"Command\(([^)]*)\)", path.read_text(encoding="utf-8")):
            registered.update(re.findall(r"[\"']([a-z][a-z0-9_]*)[\"']", arguments))
    assert registered <= ALL_DOCUMENTED_COMMANDS
    assert (registered - OPERATOR_COMMANDS) - PUBLIC_COMMANDS == set()
    assert set(registered) <= set(COMMAND_CLASSIFICATION)
    assert {"market", "trading", "copy", "intelligence", "research", "scanner",
            "watchlist", "alerts", "premium", "settings", "system", "account", "ai", "live"} == set(HELP_CATALOG)
    assert [name for name, _ in MAIN_MENU_COMMANDS] == [
        "start", "help", "analyze", "scanner", "watchlist", "trade", "journal", "copy",
        "positions", "rankings", "research", "settings", "alerts", "premium", "profile"]
    i18n = LocalizationService()
    for language in ("en", "ru", "uk", "he", "ar"):
        for command, _ in MAIN_MENU_COMMANDS:
            value = i18n.t(f"menu.{command}", language=language)
            assert value and value not in {f"menu.{command}", "Temporarily unavailable"}


def test_ai_context_is_bounded_deterministic_and_timestamp_reusable():
    from services.ai_context_compiler import AIContextCompiler
    from services.ai_intelligence import AIObservationIntelligence
    from services.ai_trading import AIContext, checksum

    def context(stamp: str):
        return AIContext(
            telegram_id=9, signal_id=10, symbol="BTCUSDT", timeframe="1h",
            market_timestamp=stamp, market={"price": 100, "entry": 100, "stop": 95,
                                             "take_profits": [105, 110], "expected_rr": 2},
            features={"market_intelligence_v3": {"signal_quality_v4": {
                "overall_quality": 72, "family_scores": {"STRUCTURE": 80}}}},
            portfolio={"count": 0, "open_positions": []},
            history={"similar_trades": [{"result": "WIN", "realized_r": 2}] * 100,
                     "prior_ai_decisions": [{"recommended_action": "ABSTAIN"}] * 100},
            deterministic={"direction": "LONG", "status": "ACTIVE", "setup_family": "BREAKOUT"},
            market_checksum=checksum({"price": 100}), feature_checksum=checksum({"quality": 72}))

    compiler = AIContextCompiler(5000)
    first = compiler.compile(context("2026-01-01T00:00:00+00:00"))
    again = compiler.compile(context("2026-01-01T00:00:00+00:00"))
    later = compiler.compile(context("2026-01-01T00:01:00+00:00"))
    assert first.payload == again.payload and first.fits_budget
    assert first.payload["tier_1_mandatory"]["identity"]["signal_id"] == 10
    assert len((first.payload.get("tier_3_conditional") or {}).get("prior_ai") or []) <= 3
    assert AIObservationIntelligence.material_state_checksum(context("2026-01-01T00:00:00+00:00"), first.payload) == \
           AIObservationIntelligence.material_state_checksum(context("2026-01-01T00:01:00+00:00"), later.payload)


def test_credentials_are_versioned_encrypted_audited_and_never_enable_live(v103_db, monkeypatch):
    from database.database import connect
    from services.exchanges.credentials_store import CredentialCipher, UserExchangeCredentialStore
    from services.exchanges.models import ExchangeName
    from services.live_accounts import LiveAccountRepository, LiveAccountState

    cipher = CredentialCipher(Fernet.generate_key().decode(), key_version="v7")
    store = UserExchangeCredentialStore(cipher)
    store.save(33, ExchangeName.BINGX, "visible-key", "super-secret", testnet=True)
    account = LiveAccountRepository().ensure(33, "bingx")
    assert account.lifecycle_state == "READ_ONLY_CONNECTED"
    assert LiveAccountRepository.transition_allowed(account.lifecycle_state,
                                                    LiveAccountState.PREFLIGHT_READY.value)
    assert not LiveAccountRepository.transition_allowed(account.lifecycle_state,
                                                        LiveAccountState.LIVE_ENABLED.value)
    assert account.live_enabled is False and account.kill_switch is True
    restored = store.get(33, ExchangeName.BINGX)
    assert restored.credentials.api_secret == "super-secret"
    with connect() as conn:
        row = dict(conn.execute("SELECT * FROM user_exchange_credentials").fetchone())
        audit = "\n".join(str(item[0]) for item in conn.execute(
            "SELECT metadata_json FROM live_audit_events").fetchall())
    assert row["key_version"] == "v7" and row["key_fingerprint"]
    assert "visible-key" not in row["api_key_encrypted"]
    assert "super-secret" not in json.dumps(row) and "super-secret" not in audit
    assert "CREDENTIAL_ACCESSED" in [item["event_type"] for item in _audit_rows()]


def _audit_rows():
    from database.database import connect
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM live_audit_events ORDER BY id").fetchall()]


def test_live_risk_profile_global_ceilings_and_complete_context(v103_db, monkeypatch):
    from services.exchanges.models import ExchangeOrderRequest
    from services.live_accounts import LiveAccountRepository
    from services.live_safety import LiveRiskRepository

    account = LiveAccountRepository().ensure(44, "bingx")
    risk = LiveRiskRepository()
    profile = risk.configure(
        account_id=account.id, telegram_id=44, max_positions=2,
        max_order_notional=Decimal("50"), max_portfolio_exposure=Decimal("200"),
        max_symbol_exposure=Decimal("100"), max_daily_realized_loss=Decimal("20"),
        max_daily_total_loss=Decimal("30"), max_modeled_slippage_bps=Decimal("20"),
        cooldown_seconds=60, allowed_symbols=["BTCUSDT"], blocked_symbols=[],
        allowed_timeframes=["1h"], allowed_strategies=["BREAKOUT"],
        allowed_directions=["BUY", "SELL"], leverage_cap=2)
    request = ExchangeOrderRequest("BTCUSDT", "BUY", "LIMIT", Decimal("0.1"), "client",
                                   price=Decimal("100"), leverage=2)
    assert risk.evaluate(profile=profile, request=request, current_positions=0,
                         modeled_slippage_bps=Decimal("5"), timeframe="1h", strategy="BREAKOUT",
                         daily_realized_loss=Decimal("0"), daily_total_loss=Decimal("0"),
                         seconds_since_last_entry=120) == ()
    assert "MAX_POSITION_COUNT_EXCEEDED" in risk.evaluate(
        profile=profile, request=request, current_positions=2,
        current_portfolio_exposure=Decimal("0"), current_symbol_exposure=Decimal("0"),
        modeled_slippage_bps=Decimal("5"), timeframe="1h", strategy="BREAKOUT",
        daily_realized_loss=Decimal("0"), daily_total_loss=Decimal("0"),
        seconds_since_last_entry=120)
    unresolved = risk.evaluate(profile=profile, request=request, timeframe="1h", strategy="BREAKOUT")
    assert "MAX_MODELED_SLIPPAGE_UNRESOLVED" in unresolved
    assert "MAX_DAILY_TOTAL_LOSS_UNRESOLVED" in unresolved
    with pytest.raises(ValueError, match="EXCEEDS_GLOBAL_CEILING"):
        risk.configure(
            account_id=account.id, telegram_id=44, max_positions=99,
            max_order_notional=Decimal("50"), max_portfolio_exposure=Decimal("200"),
            max_symbol_exposure=Decimal("100"), max_daily_realized_loss=Decimal("20"),
            max_daily_total_loss=Decimal("30"), max_modeled_slippage_bps=Decimal("20"),
            cooldown_seconds=60, allowed_symbols=["BTCUSDT"], blocked_symbols=[],
            allowed_timeframes=["1h"], allowed_strategies=["BREAKOUT"],
            allowed_directions=["BUY"], leverage_cap=2)
    with pytest.raises(ValueError, match="COOLDOWN_SECONDS_BELOW_GLOBAL_FLOOR"):
        risk.configure(
            account_id=account.id, telegram_id=44, max_positions=2,
            max_order_notional=Decimal("50"), max_portfolio_exposure=Decimal("200"),
            max_symbol_exposure=Decimal("100"), max_daily_realized_loss=Decimal("20"),
            max_daily_total_loss=Decimal("30"), max_modeled_slippage_bps=Decimal("20"),
            cooldown_seconds=1, allowed_symbols=["BTCUSDT"], blocked_symbols=[],
            allowed_timeframes=["1h"], allowed_strategies=["BREAKOUT"],
            allowed_directions=["BUY"], leverage_cap=2)


def test_kill_switch_scopes_fail_closed(v103_db, monkeypatch):
    from services.live_safety import LiveKillSwitchRepository

    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_EXCHANGE_BINGX_ENABLED", "true")
    switches = LiveKillSwitchRepository()
    assert switches.blockers(exchange="bingx", telegram_id=5, account_id=7) == ()
    for scope, key in (("GLOBAL", "GLOBAL"), ("EXCHANGE", "bingx"),
                       ("USER", "5"), ("CONNECTION", "7")):
        switches.set(scope=scope, scope_key=key, active=True, reason_code="TEST")
        assert any(scope in blocker for blocker in switches.blockers(
            exchange="bingx", telegram_id=5, account_id=7))
        switches.set(scope=scope, scope_key=key, active=False, reason_code="RELEASED")


def test_order_intent_is_immutable_and_conflicts_fail_closed(v103_db):
    from services.execution_models import ExecutionMode
    from services.exchanges.models import ExchangeOrderRequest
    from services.live_execution import LiveExecutionRepository

    repo = LiveExecutionRepository()
    request = ExchangeOrderRequest("BTCUSDT", "BUY", "LIMIT", Decimal("1"), "lv-1",
                                   price=Decimal("100"))
    first = repo.create(execution_key="same", plan_id="p1", telegram_id=1, account_id=1,
                        exchange="bingx", mode=ExecutionMode.PAPER, request=request,
                        signal_id=10, strategy="BREAKOUT")
    second = repo.create(execution_key="same", plan_id="p1", telegram_id=1, account_id=1,
                         exchange="bingx", mode=ExecutionMode.PAPER, request=request,
                         signal_id=10, strategy="BREAKOUT")
    assert first["id"] == second["id"]
    assert first["position_side"] is None and first["leverage"] == 1
    changed = ExchangeOrderRequest("BTCUSDT", "BUY", "LIMIT", Decimal("2"), "lv-1",
                                   price=Decimal("100"))
    with pytest.raises(PermissionError, match="IDEMPOTENCY_CONFLICT"):
        repo.create(execution_key="same", plan_id="p1", telegram_id=1, account_id=1,
                    exchange="bingx", mode=ExecutionMode.PAPER, request=changed,
                    signal_id=10, strategy="BREAKOUT")


def test_production_live_authority_requires_matching_claimed_approved_plan(v103_db):
    from database.database import connect
    from services.exchanges.models import ExchangeOrderRequest
    from services.live_accounts import LiveAccountRepository
    from services.live_execution import LiveExecutionCoordinator

    account = LiveAccountRepository().ensure(66, "bingx")
    payload = {"status": "APPROVED", "symbol": "BTCUSDT", "side": "BUY",
               "timeframe": "1h", "quantity": 1, "entry_price": 100, "leverage": 2}
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("""INSERT INTO copy_execution_journal(idempotency_key,plan_id,telegram_id,
            signal_id,exchange_account_id,status,code,reason,plan_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
            "approved-plan", "approved-plan", 66, 901, account.id, "EXECUTING", "APPROVED",
            "deterministic policy approved", json.dumps(payload), now, now))
    request = ExchangeOrderRequest("BTC-USDT", "BUY", "MARKET", Decimal("1"), "client-plan",
                                   price=Decimal("100"), leverage=2)
    LiveExecutionCoordinator._require_approved_execution_plan(
        plan_id="approved-plan", telegram_id=66, signal_id=901,
        account_id=account.id, request=request, timeframe="1h")
    with pytest.raises(PermissionError, match="INTENT_MISMATCH"):
        LiveExecutionCoordinator._require_approved_execution_plan(
            plan_id="approved-plan", telegram_id=66, signal_id=901,
            account_id=account.id, request=ExchangeOrderRequest(
                "BTCUSDT", "BUY", "MARKET", Decimal("2"), "client-plan",
                price=Decimal("100"), leverage=2), timeframe="1h")


@pytest.mark.asyncio
async def test_reconciliation_unknown_exchange_position_blocks_connection(v103_db, monkeypatch):
    from database.database import connect
    from services.exchanges.base import ExchangeAdapter
    from services.exchanges.models import (ExchangeCapabilities, ExchangePosition,
                                           ExchangeCapability)
    from services.live_accounts import LiveAccountRepository
    from services.live_reconciliation import LiveReconciliationService

    class Adapter(ExchangeAdapter):
        def capabilities(self):
            return ExchangeCapabilities(frozenset(ExchangeCapability))
        async def health(self): raise AssertionError
        async def balances(self): return []
        async def positions(self):
            return [ExchangePosition("BTCUSDT", "LONG", Decimal("1"), Decimal("100"),
                                     Decimal("101"), Decimal("1"), 1)]
        async def open_orders(self, symbol=None): return []
        async def symbol_rules(self, symbol): raise AssertionError

    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_EXCHANGE_BINGX_ENABLED", "true")
    account = LiveAccountRepository().ensure(77, "bingx")
    report = await LiveReconciliationService().reconcile(
        adapter=Adapter(), telegram_id=77, account_id=account.id, exchange="bingx")
    assert report["status"] == "MISMATCH"
    assert report["mismatches"][0]["type"] == "UNKNOWN_EXCHANGE_POSITION"
    with connect() as conn:
        state = conn.execute("SELECT kill_switch,live_enabled,lifecycle_state FROM live_exchange_accounts WHERE id=?",
                             (account.id,)).fetchone()
    assert state["kill_switch"] == 1 and state["live_enabled"] == 0
    assert state["lifecycle_state"] == "SUSPENDED"


@pytest.mark.asyncio
async def test_reconciliation_matches_persisted_filled_position(v103_db):
    from database.database import connect
    from services.execution_models import ExecutionMode
    from services.exchanges.base import ExchangeAdapter
    from services.exchanges.models import (ExchangeFill, ExchangeOrderRequest, ExchangePosition)
    from services.live_accounts import LiveAccountRepository
    from services.live_execution import LiveExecutionRepository
    from services.live_reconciliation import LiveReconciliationService

    class Adapter(ExchangeAdapter):
        async def health(self): raise AssertionError
        async def balances(self): return []
        async def positions(self):
            return [ExchangePosition("BTCUSDT", "LONG", Decimal("1"), Decimal("100"),
                                     Decimal("101"), Decimal("1"), 1)]
        async def open_orders(self, symbol=None): return []
        async def symbol_rules(self, symbol): raise AssertionError

    account = LiveAccountRepository().ensure(78, "bingx")
    request = ExchangeOrderRequest("BTCUSDT", "BUY", "MARKET", Decimal("1"), "filled-1",
                                   price=Decimal("100"), position_side="LONG")
    repository = LiveExecutionRepository()
    execution = repository.create(
        execution_key="filled-position", plan_id="p", telegram_id=78, account_id=account.id,
        exchange="bingx", mode=ExecutionMode.PAPER, request=request)
    repository.ingest_fills(execution, [ExchangeFill(
        "fill-1", "order-1", "filled-1", "BTCUSDT", "BUY", Decimal("1"), Decimal("100"))])
    with connect() as conn:
        conn.execute("UPDATE live_executions SET state='FILLED' WHERE id=?",
                     (execution["id"],))
        position = conn.execute("SELECT * FROM live_positions WHERE account_id=?",
                                (account.id,)).fetchone()
    assert position["position_side"] == "LONG" and position["quantity"] == 1
    report = await LiveReconciliationService().reconcile(
        adapter=Adapter(), telegram_id=78, account_id=account.id, exchange="bingx")
    assert report["status"] == "MATCHED" and report["mismatches"] == []


def test_critical_live_alert_bypasses_cosmetic_preferences_and_entitlement(v103_db):
    from services.intelligence_alerts import IntelligenceAlertService

    service = IntelligenceAlertService()
    service.preferences.get = lambda _telegram_id: {"notification_categories": []}
    service.capabilities.has = lambda _telegram_id, _capability: False
    service.usage.consume = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("critical safety alert must not consume a cosmetic plan quota"))
    decision = service.evaluate(
        91, symbol="BINGX", timeframe="account", alert_type="RECONCILIATION_MISMATCH",
        state_identity="POSITION_QTY_MISMATCH", severity="CRITICAL")
    assert decision["status"] == "ELIGIBLE"
    assert decision["critical_live_override"] is True


def test_user_analytics_export_is_user_scoped_bounded_and_secret_free(v103_db):
    from services.user_analytics_export import UserAnalyticsExportService

    service = UserAnalyticsExportService()
    json_name, json_payload = service.build(123, format_name="json", days=999)
    csv_name, csv_payload = service.build(123, format_name="csv", days=30)
    assert json_name.endswith("365d.json") and b'"resolved": 0' in json_payload
    assert csv_name.endswith("30d.csv") and b"section,key,sample" in csv_payload
    assert service.safety_contract() == {
        "user_scoped": True, "contains_credentials": False,
        "contains_provider_secrets": False, "contains_hidden_ai_reasoning": False,
        "paper_live_mixed": False, "economic_authority": False}


def test_ai_and_research_have_no_live_adapter_dependency():
    for relative in ("services/ai_trading.py", "services/ai_intelligence.py",
                     "services/research_engine.py", "services/edge_discovery.py"):
        source = (Path(__file__).parents[1] / relative).read_text(encoding="utf-8")
        assert "LiveExecutionCoordinator" not in source
        assert ".place_order(" not in source

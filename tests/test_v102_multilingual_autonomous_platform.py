from __future__ import annotations

import json

import pandas as pd
import pytest


@pytest.fixture()
def v102_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "v102.db")
    from database.database import create_tables
    create_tables()
    return tmp_path / "v102.db"


def _frame(rows: int = 90) -> pd.DataFrame:
    close = []
    value = 100.0
    for index in range(rows):
        drift = .14 if index < rows - 9 else (.04 if index < rows - 4 else .42)
        value += drift
        close.append(value)
    return pd.DataFrame({
        "open": [value - .12 for value in close],
        "high": [value + .35 for value in close],
        "low": [value - .32 for value in close],
        "close": close,
        "volume": [1000 + index * 8 for index in range(rows)],
    })


def _plan() -> dict:
    return {"entry": 111.5, "stop": 108.5, "rr": 2.0, "direction": "LONG",
            "execution_cost_quality": 76, "htf_context_score": 72}


def test_owner_is_fail_closed_audited_and_not_auto_elite(v102_db, monkeypatch):
    for name in ("ADMIN_ID", "ADMIN_IDS", "TELEGRAM_OPERATOR_USER_IDS",
                 "TELEGRAM_PLAN_ADMIN_USER_IDS", "TELEGRAM_SYSTEM_ADMIN_USER_IDS",
                 "TELEGRAM_RESEARCH_ADMIN_USER_IDS", "TELEGRAM_AI_ADMIN_USER_IDS"):
        monkeypatch.delenv(name, raising=False)
    from database.database import connect
    from services.capabilities import CapabilityService
    from services.operator_authorization import (
        ALL_OPERATOR_CAPABILITIES, OWNER_TELEGRAM_ID, OperatorAuthorizationService,
        OperatorCapability,
    )

    operators = OperatorAuthorizationService()
    assert OWNER_TELEGRAM_ID == 7975010097
    assert operators.capabilities(OWNER_TELEGRAM_ID) == ALL_OPERATOR_CAPABILITIES
    assert operators.capabilities(123) == frozenset()
    assert CapabilityService().plan(OWNER_TELEGRAM_ID)["plan"] == "FREE"
    assert operators.authorize(actor_telegram_id=123, capability=OperatorCapability.PLAN_ADMIN,
                               action="TEST_DENIED", target_telegram_id=7) is False
    assert operators.authorize(actor_telegram_id=OWNER_TELEGRAM_ID,
                               capability=OperatorCapability.PLAN_ADMIN,
                               action="TEST_ALLOWED", target_telegram_id=7) is True
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT action,outcome,target_telegram_id FROM operator_audit_events ORDER BY id"
        ).fetchall()]
    assert rows == [
        {"action": "TEST_DENIED", "outcome": "DENIED", "target_telegram_id": 7},
        {"action": "TEST_ALLOWED", "outcome": "AUTHORIZED", "target_telegram_id": 7},
    ]


def test_plan_audit_captures_previous_and_new_state(v102_db):
    from database.database import connect
    from services.capabilities import CapabilityService

    service = CapabilityService()
    granted = service.assign_plan(44, "PRO", source="ADMIN_GRANT", actor_telegram_id=7975010097,
                                  duration_days=30)
    assert granted["plan"] == "PRO" and granted["source"] == "ADMIN_GRANT"
    service.assign_plan(44, "ELITE", source="ADMIN_GRANT", actor_telegram_id=7975010097,
                        duration_days=10)
    with connect() as conn:
        raw = conn.execute("""SELECT metadata_json FROM entitlement_audit_events
            WHERE telegram_id=44 AND event_type='PLAN_GRANTED' ORDER BY id DESC LIMIT 1""").fetchone()[0]
    metadata = json.loads(raw)
    assert metadata["previous_plan"] == "PRO"
    assert metadata["previous_expires_at"]


@pytest.mark.parametrize("language", ["en", "ru", "uk", "he", "ar"])
def test_core_localization_is_explicit_and_never_leaks_keys(v102_db, language):
    from services.localization import LocalizationService, REQUIRED_CORE_KEYS, TRANSLATIONS

    i18n = LocalizationService()
    assert REQUIRED_CORE_KEYS <= set(TRANSLATIONS[language])
    for key in REQUIRED_CORE_KEYS:
        rendered = i18n.t(key, language=language, name="Trader", plan="FREE")
        assert rendered and rendered != key
    assert i18n.t("missing.internal.key", language=language) == "Temporarily unavailable"


def test_language_persists_and_rtl_market_tokens_are_isolated(v102_db):
    from services.localization import LocalizationService

    first = LocalizationService()
    assert first.set_language(55, "עברית") == "he"
    second = LocalizationService()
    assert second.language(55) == "he"
    rendered = second.market_token("BTCUSDT 123.45", language="he")
    assert rendered.startswith("\u2066") and rendered.endswith("\u2069")
    assert "BTCUSDT 123.45" in rendered
    with pytest.raises(ValueError):
        second.set_language(55, "de")


def test_market_intelligence_v102_separates_scores_and_fusion():
    from services.market_intelligence import MarketIntelligenceEngine

    frame = _frame()
    benchmark = _frame().assign(close=lambda data: data["close"] * .96)
    result = MarketIntelligenceEngine().analyze_timeframe(
        frame, timeframe="1h", side="LONG", plan=_plan(), benchmark=benchmark,
        funding_oi={"funding_rate": .0002, "open_interest": 1000,
                    "open_interest_change_pct": 2.1, "price_change_pct": 1.4,
                    "funding_history": [.0001, .00012, .00014, .0002],
                    "open_interest_history": [900, 930, 960, 1000]},
    )
    quality = result["signal_quality_v4"]
    assert set(quality["quality_dimensions"]) == {"setup", "entry", "market", "execution", "data_confidence"}
    readiness = result["entry_readiness"]
    assert readiness["version"] == "entry-readiness-v3"
    assert set(readiness["components"]) == {
        "LOCATION", "TRIGGER", "MOMENTUM", "MICROSTRUCTURE", "INVALIDATION",
        "REWARD_AFTER_COST",
    }
    fusion = result["strategy_fusion_v2"]
    assert fusion["primary"]["strategy"] != fusion["secondary"]["strategy"]
    assert fusion["suitability_gap"] >= 0 and fusion["score_is_probability"] is False
    assert result["market_regime_v2"]["version"] == "market-regime-v2"
    assert result["momentum_reacceleration"]["version"] == "momentum-reacceleration-v2"
    assert result["relative_strength"]["benchmark_version"] == "btc-benchmark-v2"
    assert result["funding_open_interest"]["version"] == "funding-oi-research-v2"


def test_entry_readiness_v2_differentiates_location_trigger_and_data():
    from services.market_intelligence import MarketIntelligenceEngine

    base_quality = {
        "invalidation": {"valid_geometry": True}, "data_confidence": 90,
        "family_scores": {"LOCATION": 80, "MOMENTUM": 75, "MICROSTRUCTURE": 70,
                          "STRUCTURE": 78, "INVALIDATION": 85,
                          "TARGET_REALISM": 75, "EXECUTION_COST": 80},
    }
    ready = MarketIntelligenceEngine._entry_readiness(
        plan=_plan(), quality=base_quality, microstructure={"status": "AVAILABLE"},
        momentum={"state": "STRONG"}, structure={"break": "CLOSE_CONFIRMED_BREAK"},
        data_quality={"status": "GOOD"})
    chasing_quality = {**base_quality, "family_scores": {**base_quality["family_scores"], "LOCATION": 20}}
    chasing = MarketIntelligenceEngine._entry_readiness(
        plan=_plan(), quality=chasing_quality, microstructure={"status": "AVAILABLE"},
        momentum={"state": "STRONG"}, structure={"break": "CLOSE_CONFIRMED_BREAK"},
        data_quality={"status": "GOOD"})
    stale = MarketIntelligenceEngine._entry_readiness(
        plan=_plan(), quality=base_quality, microstructure={"status": "AVAILABLE"},
        momentum={"state": "STRONG"}, structure={"break": "CLOSE_CONFIRMED_BREAK"},
        data_quality={"status": "INVALID"})
    assert ready["state"] == "READY"
    assert chasing["state"] == "CHASING" and chasing["score"] < ready["score"]
    assert stale["state"] == "INSUFFICIENT_DATA" and stale["score"] <= 35


def test_alert_engine_v3_records_usage_delivery_and_unchanged_state(v102_db):
    from database.database import connect
    from services.capabilities import CapabilityService
    from services.intelligence_alerts import IntelligenceAlertService
    from services.user_preferences import UserPreferenceService

    CapabilityService().assign_plan(77, "PRO", source="TEST_OVERRIDE")
    UserPreferenceService().update(77, notification_categories=["ALL"])
    alerts = IntelligenceAlertService(debounce_minutes=30)
    first = alerts.evaluate(77, symbol="BTC", timeframe="1h", alert_type="QUALITY_CHANGE",
                            state_identity="quality:72")
    assert first["version"] == "alert-engine-v3" and first["status"] == "ELIGIBLE"
    assert alerts.mark_delivered(first["alert_key"]) is True
    repeated = alerts.evaluate(77, symbol="BTCUSDT", timeframe="1h", alert_type="QUALITY_CHANGE",
                               state_identity="quality:72")
    assert repeated["suppressed_reason"] == "UNCHANGED_STATE"
    with connect() as conn:
        row = conn.execute("SELECT status,delivered_at FROM intelligence_alert_events WHERE alert_key=?",
                           (first["alert_key"],)).fetchone()
    assert row[0] == "DELIVERED" and row[1]


def test_copy_analytics_v2_empty_state_and_public_error_sanitizer(v102_db):
    from services.paper_copy_analytics import PaperCopyAnalyticsService
    from services.public_errors import public_error_message

    report = PaperCopyAnalyticsService().report(999)
    assert report["version"] == "paper-copy-analytics-v2" and report["resolved"] == 0
    assert set(report["guardrail_counterfactuals"]) == {"MAX_SLIPPAGE", "MAX_HEAT", "LOW_CONFIDENCE"}
    secret_error = RuntimeError("provider key sk-secret and raw response")
    public = public_error_message(secret_error, context="MARKET")
    assert "secret" not in public and "raw response" not in public


def test_v102_commands_are_documented():
    from services.command_catalog import PUBLIC_COMMANDS, OPERATOR_COMMANDS

    assert {"language", "usage", "copy_analytics"} <= PUBLIC_COMMANDS
    assert {"admin", "admin_plan", "admin_plan_status", "admin_plan_revoke",
            "admin_entitlements", "admin_users", "admin_usage", "admin_ai_usage"} <= OPERATOR_COMMANDS

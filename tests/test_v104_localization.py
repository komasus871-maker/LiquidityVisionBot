from __future__ import annotations

import re

import pytest


@pytest.fixture()
def v104_locale_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "v104-locale.db")
    from database.database import create_tables
    create_tables()
    return tmp_path / "v104-locale.db"


class RecordingBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))


def _enable_alerts(user_id: int, language: str):
    from services.capabilities import CapabilityService
    from services.localization import LocalizationService
    from services.user_preferences import UserPreferenceService

    CapabilityService().assign_plan(user_id, "PRO", source="TEST_OVERRIDE")
    LocalizationService().set_language(user_id, language)
    UserPreferenceService().update(user_id, notification_categories=["ALL"])


@pytest.mark.parametrize("language", ["en", "ru", "uk", "he", "ar"])
def test_v104_primary_localization_coverage_is_explicit(v104_locale_db, language):
    from services.localization import (
        LocalizationService, TRANSLATIONS, V104_PRIMARY_KEYS,
    )

    assert V104_PRIMARY_KEYS <= set(TRANSLATIONS[language])
    report = LocalizationService.coverage_report(V104_PRIMARY_KEYS)
    assert report["locales"][language]["coverage_pct"] == 100
    assert report["locales"][language]["missing"] == []
    assert report["fallback_order"] == "SELECTED_LOCALE_THEN_ENGLISH"


def test_selected_locale_persists_and_unknown_key_falls_back_to_english(v104_locale_db, caplog):
    from services.localization import LocalizationService

    i18n = LocalizationService()
    i18n.set_language(91, "he")
    assert LocalizationService().language(91) == "he"
    with caplog.at_level("WARNING"):
        assert i18n.t("not.a.public.key", language="he") == "Temporarily unavailable"
    assert "fallback=en" in caplog.text and "localization_unknown_key" in caplog.text


@pytest.mark.asyncio
async def test_lifecycle_background_notification_uses_english_without_legacy_russian(v104_locale_db):
    from services.notifier import Notifier

    _enable_alerts(101, "en")
    bot = RecordingBot()
    signal = {"id": 1, "symbol": "BTCUSDT", "side": "LONG", "status": "ACTIVE",
              "owner_telegram_id": 101, "entry": 100, "stop": 95,
              "tp1": 105, "tp2": 110, "tp3": 115, "confidence": 75,
              "timeframe": "1h", "setup_key": "BREAKOUT"}
    await Notifier(bot).lifecycle(signal, "ACTIVE", 101)
    assert len(bot.messages) == 1
    text = bot.messages[0][1]
    assert "Setup activated" in text and "Targets" in text
    assert not re.search(r"[А-Яа-яЁё]", text)


@pytest.mark.asyncio
async def test_lifecycle_background_notification_uses_hebrew_and_ltr_islands(v104_locale_db):
    from services.notifier import Notifier

    _enable_alerts(102, "he")
    bot = RecordingBot()
    signal = {"id": 2, "symbol": "BTCUSDT", "side": "LONG", "status": "ACTIVE",
              "owner_telegram_id": 102, "entry": 64500, "stop": 63000,
              "tp1": 65500, "tp2": 66500, "tp3": 68000, "confidence": 75,
              "timeframe": "1h", "setup_key": "BREAKOUT"}
    await Notifier(bot).lifecycle(signal, "ACTIVE", 65000)
    text = bot.messages[0][1]
    assert "התרחיש הופעל" in text and "יעדים" in text
    assert "\u2066" in text and "BTCUSDT" in text


@pytest.mark.asyncio
async def test_provider_background_alert_uses_persisted_arabic(v104_locale_db):
    from database.database import connect
    from services.microstructure_observer import MicrostructureObserver

    _enable_alerts(103, "ar")
    with connect() as conn:
        conn.execute("INSERT INTO user_watchlist(telegram_id,symbol,timeframe) VALUES(?,?,?)",
                     (103, "BTCUSDT", "1h"))
    bot = RecordingBot()
    observer = MicrostructureObserver(bot=bot)
    await observer._deliver_provider_alerts(
        ["BTCUSDT"], {"DEPTH": {"failed": 1}, "FUNDING": {"failed": 0},
                      "OPEN_INTEREST": {"failed": 0}})
    assert len(bot.messages) == 1
    assert "تدهور بيانات السوق" in bot.messages[0][1]
    assert "BTCUSDT" in bot.messages[0][1] and "\u2066" in bot.messages[0][1]


def test_rtl_v2_preserves_complex_market_tokens(v104_locale_db):
    from services.localization import LocalizationService

    value = "BTCUSDT $64,500 +2.31% 1.5R 15M 1H"
    for language in ("he", "ar"):
        rendered = LocalizationService().market_token(value, language=language)
        assert rendered == "\u2066" + value + "\u2069"


def test_help_live_has_localized_safety_lifecycle(v104_locale_db):
    from services.command_catalog import category_text

    assert "NOT_CONNECTED" in category_text("live", language="uk")
    assert "не вмикають торгівлю" in category_text("live", language="uk")
    assert "אינם מפעילים מסחר" in category_text("live", language="he")


def test_alert_engine_v4_critical_live_bypasses_cosmetic_preferences(v104_locale_db):
    from services.intelligence_alerts import IntelligenceAlertService
    from services.user_preferences import UserPreferenceService

    UserPreferenceService().update(104, notification_categories=[])
    result = IntelligenceAlertService().evaluate(
        104, symbol="BINGX", timeframe="account", alert_type="KILL_SWITCH",
        state_identity="connection:9", severity="CRITICAL")
    assert result["version"] == "alert-engine-v4"
    assert result["status"] == "ELIGIBLE" and result["critical_live_override"] is True


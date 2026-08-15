from __future__ import annotations

from datetime import datetime, timezone
import re
from pathlib import Path


def test_context_compiler_reduces_realistic_v10_context_and_preserves_safety_fields():
    from services.ai_context_compiler import AIContextCompiler
    from services.ai_trading import AIContext, checksum

    huge = [{"evidence": "verbose internal explanation " * 30, "value": index}
            for index in range(250)]
    intelligence = {
        "market_story": {"state": "BREAKOUT_ATTEMPT", "transition": "RANGE->BREAKOUT_ATTEMPT",
                         "facts": ["close-confirmed structure break"] * 20},
        "structure_quality": {"direction": "BULLISH", "break": "BOS", "quality": 88},
        "quality": {"overall_quality": 71, "market_quality": 75,
                    "family_scores": {"STRUCTURE": 88, "LIQUIDITY": 82, "MOMENTUM": 69},
                    "contradicting_evidence": [{"severity": "HIGH", "reason": "late entry"}] * 20,
                    "uncertainties": ["BENCHMARK_CONTEXT_UNAVAILABLE"],
                    "invalidation": {"valid_geometry": True, "distance_atr": 1.2}},
        "liquidity": {"likely_attractor": 110, "above": huge, "below": huge},
        "microstructure": {"status": "AVAILABLE", "spread_bps": 2.1,
                           "behavior_labels": ["DEPTH_IMBALANCE_BID"]},
        "funding_open_interest": {"status": "AVAILABLE", "funding_rate": "0.0001",
                                  "open_interest": "12345"},
        "relative_strength": {"status": "AVAILABLE", "state": "INDEPENDENT_STRENGTH"},
        "momentum": {"state": "ACCELERATING", "direction": "BULLISH", "score": 76},
        "trend_maturity": {"state": "HEALTHY", "direction": "BULLISH"},
    }
    context = AIContext(
        telegram_id=7, signal_id=470, symbol="BTCUSDT", timeframe="1h",
        market_timestamp=datetime.now(timezone.utc).isoformat(),
        market={"price": 100, "entry": 100, "stop": 95,
                "take_profits": [105, 110, 115], "expected_rr": 2},
        features={"market_intelligence_v2": intelligence, "verbose_internal": huge},
        portfolio={"count": 0, "open_positions": []},
        history={"similar_trades": huge, "prior_ai_decisions": huge,
                 "learned_patterns": {"evidence": huge}},
        deterministic={"direction": "LONG", "status": "ACTIVE",
                       "setup_family": "BREAKOUT", "recommendation": "READY"},
        market_checksum=checksum({"price": 100}), feature_checksum=checksum(intelligence),
    )
    compiled = AIContextCompiler(30_000).compile(context)
    tier_1 = compiled.payload["tier_1_mandatory"]

    assert compiled.original_chars > 250_000
    assert compiled.fits_budget and compiled.compiled_chars < 30_000
    assert compiled.compiled_chars < compiled.original_chars * .08
    assert tier_1["identity"]["symbol"] == "BTCUSDT"
    assert tier_1["market"]["stop"] == 95
    assert tier_1["invalidation"]["valid_geometry"] is True
    assert tier_1["target_geometry"]["expected_rr"] == 2
    assert tier_1["strongest_contradictions"]
    assert "tier_4_verbose_internal_state" in compiled.sections_omitted


def test_help_catalog_covers_every_registered_command():
    from services.command_catalog import ALL_DOCUMENTED_COMMANDS, HELP_CATALOG

    registered: set[str] = set()
    for path in (Path(__file__).parents[1] / "handlers").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for arguments in re.findall(r"Command\(([^)]*)\)", source):
            registered.update(re.findall(r"[\"']([a-z][a-z0-9_]*)[\"']", arguments))
    assert registered <= ALL_DOCUMENTED_COMMANDS, sorted(registered - ALL_DOCUMENTED_COMMANDS)
    assert {"market", "trading", "copy", "intelligence", "research", "scanner",
            "watchlist", "alerts", "premium", "settings", "system", "account", "ai", "live"} == set(HELP_CATALOG)


def test_symbol_normalization_is_friendly_but_not_ambiguous():
    import pytest
    from utils.symbols import normalize_usdt_symbol

    assert normalize_usdt_symbol("btc") == "BTCUSDT"
    assert normalize_usdt_symbol("BTC-USDT") == "BTCUSDT"
    assert normalize_usdt_symbol("btcusdt") == "BTCUSDT"
    with pytest.raises(ValueError):
        normalize_usdt_symbol("BTCUSD")


def test_alert_engine_v2_persists_entitlement_and_debounce_decisions(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "alerts.db")
    from database.database import create_tables
    from services.intelligence_alerts import IntelligenceAlertService

    create_tables()
    alerts = IntelligenceAlertService(debounce_minutes=30)
    first = alerts.evaluate(88, symbol="btc", timeframe="1h", alert_type="QUALITY_IMPROVES",
                            state_identity="quality-60")
    repeat = alerts.evaluate(88, symbol="BTC-USDT", timeframe="1h", alert_type="QUALITY_IMPROVES",
                             state_identity="quality-65")
    gated = alerts.evaluate(88, symbol="BTCUSDT", timeframe="1h", alert_type="OI_ACCELERATION",
                            state_identity="oi-fast")
    assert first["status"] == "ELIGIBLE"
    assert repeat["suppressed_reason"] == "DEBOUNCE_WINDOW"
    assert gated["suppressed_reason"] == "ENTITLEMENT_REQUIRED"


def test_operational_retention_is_audited_and_targets_only_ephemeral_rows(tmp_path, monkeypatch):
    from datetime import timedelta

    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "retention.db")
    monkeypatch.setenv("FEATURE_USAGE_RETENTION_DAYS", "30")
    from database.database import connect, create_tables
    from services.operational_retention import OperationalRetentionService

    create_tables()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    with connect() as conn:
        conn.execute("""INSERT INTO feature_usage_events(event_key,telegram_id,capability,plan_key,
            outcome,provider,estimated_cost_usd,metadata_json,created_at)
            VALUES('old-usage',1,'SCANNER','FREE','ALLOWED',NULL,0,'{}',?)""", (old,))
    report = OperationalRetentionService().run()
    with connect() as conn:
        remaining = conn.execute("SELECT COUNT(*) n FROM feature_usage_events").fetchone()["n"]
        audits = conn.execute("SELECT COUNT(*) n FROM operational_retention_runs").fetchone()["n"]
    assert report["status"] == "COMPLETE"
    assert remaining == 0 and audits == 1

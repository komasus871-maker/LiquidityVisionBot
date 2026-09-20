from __future__ import annotations

import hashlib
import hmac
import inspect
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import database.database as database
from keyboards.main_menu import main_keyboard
from services.command_catalog import (
    FUNCTION_REGISTRY, OPERATOR_COMMANDS, audit_handler_commands, category_text,
    help_functions, registry_counts,
)
from services.pump_dump_scanner import (
    Candle, PumpDumpScanner, ScannerRepository, ScannerSettings, Severity,
    build_symbol_snapshot, calculate_rsi, classify_additional_alerts,
    percent_change, render_alert_card, resource_budget, select_liquid_universe,
)
from services.telegram_webapp import terminal_html, validate_init_data
from services.trade_confirmation import (
    META_CONFIRMATION_EXPERIMENT_ID, MetaGateResearch, TradeConfirmationEngine,
)


def _sqlite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "USE_POSTGRES", False)
    monkeypatch.setattr(database, "REQUIRE_PERSISTENT_DB", False)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DATABASE_NAME", tmp_path / "app.sqlite3")
    database.create_tables()


def _candles(move_pct: float = 8.0) -> list[Candle]:
    start = datetime(2026, 9, 20, tzinfo=timezone.utc)
    rows: list[Candle] = []
    for index in range(241):
        base = 100 + index * .005
        if index >= 236:
            base *= 1 + (move_pct / 100) * ((index - 235) / 5)
        rows.append(Candle(start + timedelta(minutes=index), base, base * 1.001,
                           base * .999, base, 1000 if index < 240 else 5000, 100 + index))
    return rows


def _snapshot(move_pct: float = 8.0):
    return build_symbol_snapshot(
        symbol="TESTUSDT", venue="BINANCE", candles=_candles(move_pct),
        quote_volume_24h=100_000_000, change_24h_pct=move_pct,
        observed_at=datetime(2026, 9, 20, 4, 1, tzinfo=timezone.utc),
    )


def test_authoritative_registry_is_complete_and_metadata_rich() -> None:
    audit = audit_handler_commands("handlers")
    assert audit["unreachable"] == ()
    assert audit["documented_without_handler"] == ()
    assert registry_counts()["TOTAL_FUNCTIONS"] == len(FUNCTION_REGISTRY)
    assert all(item.id and item.title and item.description and item.category and item.permissions
               for item in FUNCTION_REGISTRY)
    assert {"pump_scan", "scanner_settings", "deep_analyze", "terminal"} <= {
        item.command for item in FUNCTION_REGISTRY
    }


def test_help_is_generated_from_registry_and_permission_filtered() -> None:
    scanner_help = category_text("scanner")
    assert scanner_help and "/pump_scan" in scanner_help and "/scanner_settings" in scanner_help
    public_help = {item.command for item in help_functions()}
    assert not (public_help & OPERATOR_COMMANDS)
    assert category_text("admin") is None


def test_main_menu_is_bounded_and_contains_terminal_product_routes() -> None:
    labels = [button.text for row in main_keyboard().keyboard for button in row]
    assert len(labels) == 13
    assert {"📊 Markets", "⚡ Scanner", "🚀 Open Terminal", "🧪 Shadow Lab"} <= set(labels)


def test_percent_rsi_volume_and_objective_universe() -> None:
    assert round(percent_change(100, 108), 2) == 8.0
    assert calculate_rsi(list(range(1, 20))) == 100.0
    snapshot = _snapshot()
    assert snapshot.changes_pct[5] > 7
    assert snapshot.relative_volume == 5
    assert all(snapshot.rsi[window] is not None for window in (3, 5, 15))
    selected = select_liquid_universe([
        {"symbol": "AUSDT", "status": "TRADING", "contract_type": "PERPETUAL", "quote_volume": 30},
        {"symbol": "BUSDT", "status": "TRADING", "contract_type": "PERPETUAL", "quote_volume": 50},
        {"symbol": "CUSD", "status": "TRADING", "contract_type": "PERPETUAL", "quote_volume": 100},
    ], minimum_quote_volume=20, limit=2)
    assert selected == ("BUSDT", "AUSDT")


def test_scanner_detection_severity_and_market_alert_separation() -> None:
    events = PumpDumpScanner().detect(_snapshot(), ScannerSettings())
    assert events
    assert any(event["direction"] == "PUMP" and event["severity"] in {Severity.STRONG, Severity.EXTREME}
               for event in events)
    assert all(event["classification"] == "MARKET_ALERT" and event["economic_authority"] is False
               for event in events)


def test_episode_dedup_cooldown_escalation_and_24h_count(monkeypatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)
    repository = ScannerRepository()
    settings = ScannerSettings(cooldown_seconds=900, re_alert_pct=2)
    event = PumpDumpScanner().detect(_snapshot(), settings)[0]
    first = repository.admit(event, settings)
    assert first and first.signal_count_24h == 1
    assert repository.admit(event, settings) is None
    escalated = dict(event, severity=Severity.EXTREME, move_pct=event["move_pct"] + 4,
                     observed_at=event["observed_at"] + timedelta(minutes=1))
    second = repository.admit(escalated, settings)
    assert second and second.signal_count_24h == 2
    stats = repository.stats_24h(now=event["observed_at"] + timedelta(hours=1))
    assert stats["pump_events"] == 2 and stats["predictive_claim"] is False


def test_scanner_settings_persist_per_user(monkeypatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)
    expected = ScannerSettings(enabled=False, market_scope="WATCHLIST_ONLY", windows=(1, 5),
                               move_threshold_pct=4.5, minimum_severity=Severity.STRONG,
                               muted_symbols=("DOGEUSDT",))
    ScannerRepository.save_settings(42, expected)
    actual = ScannerRepository.settings(42)
    assert actual.enabled is False
    assert actual.market_scope == "WATCHLIST_ONLY"
    assert actual.windows == (1, 5)
    assert actual.minimum_severity == Severity.STRONG
    assert actual.muted_symbols == ("DOGEUSDT",)


def test_alert_card_omits_unavailable_values() -> None:
    snapshot = _snapshot()
    event = PumpDumpScanner().detect(snapshot, ScannerSettings())[0]
    from services.pump_dump_scanner import MarketAlert
    alert = MarketAlert("e", "p", snapshot.symbol, snapshot.venue, event["direction"],
                        event["severity"], event["window_minutes"], event["move_pct"],
                        event["price_start"], event["price_end"], snapshot.relative_volume,
                        event["volatility_normalized_move"], 1, snapshot.observed_at, snapshot)
    card = render_alert_card(alert)
    assert "MARKET ALERT" in card and "not an approved trade signal" in card
    assert "OI:" not in card and "Funding:" not in card and "Spread:" not in card


def test_optional_anomaly_labels_have_no_economic_authority() -> None:
    snapshot = replace(_snapshot(), oi_change_pct=8, funding_rate=.002,
                       liquidations_usd=2_000_000, cross_venue_diff_pct=.5)
    alerts = classify_additional_alerts(snapshot)
    kinds = {item["alert_type"] for item in alerts}
    assert {"OI_SHOCK", "FUNDING_EXTREME", "LIQUIDATION_CASCADE",
            "CROSS_VENUE_DISLOCATION"} <= kinds
    assert all(item["classification"] == "MARKET_ALERT" and not item["economic_authority"]
               for item in alerts)


def test_meta_confirmation_is_descriptive_and_cannot_mutate_decision() -> None:
    analysis = {"direction": "LONG", "unified_decision": {"action": "WAIT"},
                "decision_quality": {"data_confidence": 80}}
    original = json.loads(json.dumps(analysis))
    result = TradeConfirmationEngine().evaluate(
        analysis, {"spread_pct": .3, "taker_imbalance": -.8, "funding_rate": .002}
    )
    assert result.state.value == "REJECT"
    assert {"SPREAD_TOO_WIDE", "ORDER_FLOW_CONFLICT", "FUNDING_CROWDING"} <= set(result.reason_codes)
    assert result.production_gate is False and result.economic_authority is False
    assert result.source_decision_action == "WAIT"
    assert analysis == original


def test_meta_gate_research_uses_new_forward_identity_and_cannot_promote() -> None:
    records = [
        {"experiment_id": META_CONFIRMATION_EXPERIMENT_ID, "evidence_phase": "FORWARD",
         "confirmation_state": "CONFIRM", "net_r_after_recorded_costs": 1.0},
        {"experiment_id": META_CONFIRMATION_EXPERIMENT_ID, "evidence_phase": "FORWARD",
         "confirmation_state": "REJECT", "net_r_after_recorded_costs": -0.5},
        {"experiment_id": "frozen-forward-candidate", "evidence_phase": "FORWARD",
         "confirmation_state": "CONFIRM", "net_r_after_recorded_costs": 10.0},
    ]
    result = MetaGateResearch().compare(records)
    assert result["base"]["n"] == 2 and result["base_plus_meta_gate"]["n"] == 1
    assert result["retention_rate"] == .5
    assert result["qualified_for_production"] is False
    assert result["economic_authority"] is False


def _signed_init_data(token: str, now: int = 1_800_000_000) -> str:
    values = {"auth_date": str(now), "query_id": "AAE", "user": json.dumps({"id": 42, "first_name": "Ada"}, separators=(",", ":"))}
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_mini_app_init_data_authorization_and_expiry() -> None:
    token = "123456:TEST"
    value = _signed_init_data(token)
    identity = validate_init_data(value, token, now=1_800_000_010)
    assert identity.telegram_id == 42
    try:
        validate_init_data(value.replace("Ada", "Eve"), token, now=1_800_000_010)
        assert False, "tampering must fail"
    except ValueError:
        pass
    try:
        validate_init_data(value, token, max_age_seconds=60, now=1_800_000_100)
        assert False, "expired data must fail"
    except ValueError:
        pass


def test_terminal_is_read_only_bounded_and_never_scans_raw_partitions() -> None:
    html = terminal_html()
    assert "Read-only market terminal" in html
    assert "BUY" not in html and "SELL" not in html
    from services.webhook_server import WebhookServer
    source = inspect.getsource(WebhookServer.terminal_api_handler)
    assert "ForwardRuntimeStateRepository" in source
    assert "iter_partition" not in source and "/var/data" not in source
    assert resource_budget()["full_depth_subscriptions"] == 0


def test_render_and_live_invariants_remain_fail_closed() -> None:
    text = Path("render.yaml").read_text(encoding="utf-8")
    assert text.count("type: web") == 1 and text.count("type: worker") == 1
    assert text.count("LIVE_EXECUTION_ENABLED") == 2
    assert text.count('value: "false"') >= 9
    assert "PUMP_SCANNER_UNIVERSE_LIMIT" in text
    assert "python -m tools.run_forward_microstructure_collector" in text
    assert "C:\\Users" not in text and ".codex" not in text

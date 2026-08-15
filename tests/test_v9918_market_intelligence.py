from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest


def _frame(*, count: int = 240, start: float = 100.0, end: float = 120.0,
           noise: float = 1.0, timeframe_minutes: int = 60) -> pd.DataFrame:
    index = np.arange(count)
    close = np.linspace(start, end, count) + np.sin(index / 5) * noise
    open_price = close - np.cos(index / 4) * .25
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=count,
                                   freq=f"{timeframe_minutes}min", tz="UTC"),
        "open": open_price,
        "high": np.maximum(open_price, close) + .55,
        "low": np.minimum(open_price, close) - .55,
        "close": close,
        "volume": 100 + (index % 11) * 7,
        "confirm": ["1"] * count,
    })


@pytest.fixture()
def intelligence_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "intelligence.db")
    from database.database import create_tables
    create_tables()
    create_tables()
    return tmp_path


def _plan(side: str = "LONG", *, valid: bool = True) -> dict:
    if side == "LONG":
        entry, stop = 120.0, 116.0 if valid else 122.0
    else:
        entry, stop = 100.0, 104.0 if valid else 98.0
    return {"direction": side, "entry": entry, "stop": stop, "rr": 2.0,
            "tp1": entry + (2 if side == "LONG" else -2),
            "tp2": entry + (4 if side == "LONG" else -4),
            "tp3": entry + (8 if side == "LONG" else -8)}


def test_v9918_schema_is_repeatable_and_postgresql_placeholder_safe(intelligence_db, monkeypatch):
    import database.database as database_module
    from database.database import DBConnection, connect, create_tables

    create_tables()
    with connect() as conn:
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        intelligence_columns = {row["name"] for row in conn.execute(
            "PRAGMA table_info(market_intelligence_snapshots)"
        ).fetchall()}
        microstructure_columns = {row["name"] for row in conn.execute(
            "PRAGMA table_info(microstructure_aggregates)"
        ).fetchall()}
    assert {"market_intelligence_snapshots", "microstructure_aggregates"} <= tables
    assert {"snapshot_checksum", "quality_json", "full_snapshot_json"} <= intelligence_columns
    assert {"aggregate_checksum", "aggregate_json", "expires_at"} <= microstructure_columns
    translated = DBConnection._translate(
        "INSERT INTO microstructure_aggregates(symbol,status) VALUES(?,?) "
        "ON CONFLICT(aggregate_checksum) DO NOTHING"
    )
    assert translated.count("%s") == 2 and "?" not in translated
    monkeypatch.setattr(database_module, "USE_POSTGRES", True)
    assert database_module._id_column() == "BIGSERIAL PRIMARY KEY"


def test_hierarchy_roles_and_unclosed_candles_are_future_safe():
    from services.market_intelligence import MarketIntelligenceEngine

    engine = MarketIntelligenceEngine()
    base = _frame()
    poisoned = pd.concat([base, pd.DataFrame([{
        "timestamp": base.iloc[-1]["timestamp"] + pd.Timedelta(hours=1),
        "open": 120, "high": 10000, "low": 1, "close": 9000, "volume": 999999,
        "confirm": "0",
    }])], ignore_index=True)
    clean = engine.analyze_timeframe(base, timeframe="1h", side="LONG", plan=_plan())
    guarded = engine.analyze_timeframe(poisoned, timeframe="1h", side="LONG", plan=_plan())
    assert guarded["data_quality"]["unclosed_candles_removed"] == 1
    assert guarded["data_quality"]["last_candle_at"] == clean["data_quality"]["last_candle_at"]
    assert guarded["level_map"]["current_price"] == pytest.approx(clean["level_map"]["current_price"])
    assert guarded["market_story"]["state"] == clean["market_story"]["state"]
    hierarchy = engine.analyze_hierarchy({
        "4h": _frame(timeframe_minutes=240),
        "1h": base,
        "15m": _frame(timeframe_minutes=15),
    }, side="LONG", plan=_plan())
    assert hierarchy["roles"] == {"4h": "HIGHER_TIMEFRAME_CONTEXT", "1h": "SETUP_STRUCTURE",
                                  "15m": "ENTRY_REFINEMENT"}
    assert not hierarchy["missing_timeframes"]
    assert hierarchy["blind_alignment_required"] is False


def test_level_clustering_liquidity_and_zone_quality_do_not_double_count():
    from services.market_intelligence import MarketIntelligenceEngine

    result = MarketIntelligenceEngine().analyze_timeframe(
        _frame(noise=2.5), timeframe="1h", side="LONG", plan=_plan())
    levels = result["level_map"]
    assert levels["clusters"] and result["liquidity_map"]["unresolved_count"] >= 0
    assert levels["double_counting_control"].startswith("MAX_WITHIN_FAMILY")
    assert all(cluster["independent_family_count"] <= cluster["raw_representation_count"]
               for cluster in levels["clusters"])
    assert all(0 <= cluster["quality"] <= 100 for cluster in levels["clusters"])
    assert result["zone_quality"]["status"] == "AVAILABLE"
    assert all({"fresh", "mitigation_count", "origin_displacement_atr", "quality"} <= set(zone)
               for zone in result["zone_quality"]["fvg"] + result["zone_quality"]["order_blocks"])


def test_structure_sweep_momentum_and_trend_states_are_deterministic():
    from services.market_intelligence import MarketIntelligenceEngine

    result = MarketIntelligenceEngine().analyze_timeframe(
        _frame(noise=3), timeframe="15m", side="LONG", plan=_plan())
    assert result["structure_quality"]["break"] in {"NO_BREAK", "WICK_BREAK", "CLOSE_CONFIRMED_BREAK"}
    assert result["structure_quality"]["sweep"] in {
        "CLEAN_SWEEP", "WEAK_SWEEP", "BREAKOUT_NOT_SWEEP", "AMBIGUOUS"}
    assert result["momentum"]["state"] in {
        "ACCELERATING", "STRONG", "HEALTHY", "DECELERATING", "EXHAUSTING",
        "REVERSING", "CONFLICTED", "UNKNOWN"}
    assert result["trend_maturity"]["state"] in {"EARLY", "DEVELOPING", "MATURE", "EXTENDED", "EXHAUSTED"}


def test_pump_and_dump_reversal_distinguish_continuation_from_exhaustion(monkeypatch):
    from services.market_intelligence import MarketIntelligenceEngine

    monkeypatch.setenv("REVERSAL_EXTREME_MOVE_PCT", "8")
    pump = _frame(count=180, start=100, end=101, noise=.3, timeframe_minutes=60)
    pump.loc[155:165, "close"] = np.linspace(100, 150, 11)
    pump.loc[155:165, "open"] = pump.loc[155:165, "close"] - .4
    pump.loc[155:165, "high"] = pump.loc[155:165, ["open", "close"]].max(axis=1) + .5
    pump.loc[155:165, "low"] = pump.loc[155:165, ["open", "close"]].min(axis=1) - .5
    pump.loc[165:, "close"] = np.linspace(150, 145, 15)
    pump.loc[165:, "open"] = pump.loc[165:, "close"] + .4
    pump.loc[165:, "high"] = pump.loc[165:, ["open", "close"]].max(axis=1) + .5
    pump.loc[165:, "low"] = pump.loc[165:, ["open", "close"]].min(axis=1) - .5
    exhausted = MarketIntelligenceEngine().analyze_timeframe(
        pump, timeframe="1h", side="SHORT", plan=_plan("SHORT"))
    pump_candidate = exhausted["reversal_research"]["pump"]
    assert pump_candidate["state"] in {
        "PUMP_REVERSAL_EARLY", "PUMP_REVERSAL_CONFIRMED", "PUMP_REVERSAL_CONTINUATION_RISK"}

    continuation = MarketIntelligenceEngine().analyze_timeframe(
        _frame(count=180, start=100, end=155, noise=.05),
        timeframe="1h", side="SHORT", plan=_plan("SHORT"))
    continued = continuation["reversal_research"]["pump"]
    if continued["continuation_risk"]:
        assert continued["state"] == "PUMP_REVERSAL_CONTINUATION_RISK"

    dump = _frame(count=180, start=150, end=149, noise=.3)
    dump.loc[155:165, "close"] = np.linspace(150, 95, 11)
    dump.loc[155:165, "open"] = dump.loc[155:165, "close"] + .4
    dump.loc[155:165, "high"] = dump.loc[155:165, ["open", "close"]].max(axis=1) + .5
    dump.loc[155:165, "low"] = dump.loc[155:165, ["open", "close"]].min(axis=1) - .5
    dump.loc[165:, "close"] = np.linspace(95, 100, 15)
    dump.loc[165:, "open"] = dump.loc[165:, "close"] - .4
    dump.loc[165:, "high"] = dump.loc[165:, ["open", "close"]].max(axis=1) + .5
    dump.loc[165:, "low"] = dump.loc[165:, ["open", "close"]].min(axis=1) - .5
    dump_candidate = MarketIntelligenceEngine().analyze_timeframe(
        dump, timeframe="1h", side="LONG", plan=_plan("LONG"))["reversal_research"]["dump"]
    assert dump_candidate["state"] != "DUMP_REVERSAL_INVALID"
    assert dump_candidate["martingale"] is False


def test_microstructure_wall_persistence_removal_absorption_and_spoof_skepticism():
    from services.market_intelligence import OrderBookMicrostructureEngine

    persistent = [
        {"bids": [[99.9, 10], [99.8, 8], [99.5, 2]],
         "asks": [[100.1, 60], [100.2, 6], [100.5, 2]], "timestamp": index}
        for index in range(5)
    ]
    result = OrderBookMicrostructureEngine().analyze(persistent)
    assert result["status"] == "AVAILABLE" and result["raw_book_persisted"] is False
    assert any(wall["state"] in {"PERSISTENT_WALL", "REPLENISHING_WALL"} for wall in result["walls"])
    assert result["actor_identity_claimed"] is False
    assert result["absorption_inference"] == "UNCONFIRMED"
    assert result["executed_flow_available"] is False

    pulled = [
        {"bids": [[99.9, 10], [99.8, 2]], "asks": [[100.1, 80], [100.2, 2]]},
        {"bids": [[99.9, 10], [99.8, 2]], "asks": [[100.1, 3], [100.2, 2]]},
        {"bids": [[99.9, 10], [99.8, 2]], "asks": [[100.1, 3], [100.2, 2]]},
    ]
    spoof = OrderBookMicrostructureEngine().analyze(pulled)
    assert "POSSIBLE_SPOOF" in spoof["classifications"]
    assert any(wall["spoof_like"] for wall in spoof["walls"])

    executed = [dict(item, aggressive_buy_volume=100, aggressive_sell_volume=10)
                for item in persistent[:3]]
    absorption = OrderBookMicrostructureEngine().analyze(executed)
    assert absorption["executed_flow_available"] is True
    assert absorption["absorption_inference"] == "POSSIBLE_SELL_ABSORPTION"


def test_quality_decomposition_critical_invalidation_and_no_probability_claim():
    from services.market_intelligence import MarketIntelligenceEngine

    valid = MarketIntelligenceEngine().analyze_timeframe(
        _frame(), timeframe="1h", side="LONG", plan=_plan(valid=True))
    quality = valid["signal_quality_v2"]
    assert set(quality["confidence"]) == {
        "DATA_CONFIDENCE", "DIRECTION_CONFIDENCE", "SETUP_CONFIDENCE", "ENTRY_CONFIDENCE",
        "INVALIDATION_CONFIDENCE", "TARGET_CONFIDENCE", "EXECUTION_CONFIDENCE", "OVERALL_QUALITY"}
    assert quality["score_is_probability"] is False and quality["economic_authority"] is False
    assert quality["family_aggregation"] == "INDEPENDENT_FAMILY_MAX_65_PLUS_MEAN_35_COVERAGE_NORMALIZED"
    assert quality["normalized_components"] == quality["family_scores"]
    assert quality["contradiction_aggregation"].startswith("MAX_PER_FAMILY")

    invalid = MarketIntelligenceEngine().analyze_timeframe(
        _frame(), timeframe="1h", side="LONG", plan=_plan(valid=False))["signal_quality_v2"]
    assert "INVALID_STOP_GEOMETRY" in invalid["critical_disqualifiers"]
    assert invalid["overall_quality"] <= 35


def _research_row(signal_id: int, decision_at: str) -> dict:
    return {"snapshot_id": f"snapshot-{signal_id}", "signal_id": signal_id,
            "owner_telegram_id": 88, "symbol": "BTCUSDT", "timeframe": "1h",
            "side": "LONG", "decision_at": decision_at}


def _insert_research_parent(row: dict, *, capture_quality: str = "DECISION_TIME") -> None:
    from database.database import connect
    snapshot = {"signal_id": row["signal_id"], "symbol": row["symbol"],
                "timeframe": row["timeframe"], "side": row["side"], "features": {}}
    with connect() as conn:
        conn.execute("""INSERT INTO research_signal_snapshots(snapshot_id,signal_id,owner_telegram_id,
            symbol,timeframe,side,strategy_key,setup_family,decision_at,captured_at,capture_quality,
            feature_version,source_checksum,primary_regime,regimes_json,confidence_bucket,session_key,
            snapshot_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            row["snapshot_id"], row["signal_id"], row["owner_telegram_id"], row["symbol"],
            row["timeframe"], row["side"], "LIQUIDITY_SMC", "test", row["decision_at"],
            row["decision_at"], capture_quality, "research-features-v3", f"source-{row['signal_id']}",
            "UNKNOWN", "[]", "70-79", "UNKNOWN", json.dumps(snapshot)))


def test_repository_is_immutable_idempotent_restart_safe_and_tracks_story_transitions(intelligence_db):
    from database.database import connect
    from services.market_intelligence import MarketIntelligenceEngine
    from services.market_intelligence_repository import MarketIntelligenceRepository

    repo = MarketIntelligenceRepository()
    first_intelligence = MarketIntelligenceEngine().analyze_timeframe(
        _frame(), timeframe="1h", side="LONG", plan=_plan())
    first = _research_row(5001, "2026-02-01T00:00:00+00:00")
    features = {"extras": {"market_intelligence": first_intelligence}}
    assert repo.persist_signal(first, features)
    assert repo.persist_signal(first, features)
    second_intelligence = MarketIntelligenceEngine().analyze_timeframe(
        _frame(start=120, end=100), timeframe="1h", side="SHORT", plan=_plan("SHORT"))
    second = _research_row(5002, "2026-02-02T00:00:00+00:00")
    second["side"] = "SHORT"
    stored_second = repo.persist_signal(second, {"market_intelligence": second_intelligence})
    assert stored_second["story"]["previous_state"] == repo.get_signal(5001, 88)["story"]["state"]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM market_intelligence_snapshots").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) n FROM research_signal_rankings WHERE rank_version='signal-ranking-v5'").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) n FROM paper_execution_orders").fetchone()["n"] == 0


def test_microstructure_repository_persists_only_aggregate_and_marks_stale(intelligence_db):
    from services.market_intelligence import MarketIntelligenceEngine, OrderBookMicrostructureEngine
    from services.market_intelligence_repository import MarketIntelligenceRepository

    aggregate = OrderBookMicrostructureEngine().analyze([
        {"bids": [[99, 10], [98, 2]], "asks": [[101, 8], [102, 2]]}
        for _ in range(3)
    ])
    aggregate["funding_open_interest"] = {
        "funding_rate": "0.0001", "open_interest": "12345",
    }
    repo = MarketIntelligenceRepository()
    sampled = "2026-01-01T00:00:00+00:00"
    assert repo.persist_microstructure(symbol="BTCUSDT", exchange="bingx", environment="prod-live",
                                       aggregate=aggregate, sampled_at=sampled, ttl_seconds=60)
    assert not repo.persist_microstructure(symbol="BTCUSDT", exchange="bingx", environment="prod-live",
                                           aggregate=aggregate, sampled_at=sampled, ttl_seconds=60)
    row = repo.latest_microstructure("BTC-USDT")
    assert row["aggregate"]["raw_book_persisted"] is False
    assert row["stale"] is True
    assert "bids" not in row["aggregate"] and "asks" not in row["aggregate"]
    with pytest.raises(ValueError, match="raw order books"):
        repo.persist_microstructure(
            symbol="BTCUSDT", exchange="bingx", environment="prod-live",
            aggregate={**aggregate, "bids": [[99, 10]]}, sampled_at=sampled,
        )
    rejected = MarketIntelligenceEngine().analyze_timeframe(
        _frame(), timeframe="1h", side="LONG", plan=_plan(),
        microstructure_aggregate={**aggregate, "bids": [[99, 10]]},
    )
    assert rejected["microstructure"]["status"] == "UNAVAILABLE"
    current = datetime.now(timezone.utc).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert repo.persist_microstructure(symbol="BTCUSDT", exchange="bingx", environment="prod-live",
                                       aggregate=aggregate, sampled_at=current, ttl_seconds=300)
    assert repo.persist_microstructure(symbol="BTCUSDT", exchange="bingx", environment="prod-live",
                                       aggregate=aggregate, sampled_at=future, ttl_seconds=300)
    decision_time_row = repo.latest_microstructure("BTCUSDT")
    assert decision_time_row["sampled_at"] == current and decision_time_row["stale"] is False
    enriched = MarketIntelligenceEngine().analyze_timeframe(
        _frame(), timeframe="1h", side="LONG", plan=_plan(),
        microstructure_aggregate={**decision_time_row["aggregate"], "sampled_at": current},
    )
    assert enriched["microstructure"]["decision_time_source"] == "FRESH_PERSISTED_PUBLIC_AGGREGATE"
    assert enriched["funding_open_interest"]["status"] == "AVAILABLE"


def test_quality_threshold_curves_count_missed_winners_and_avoided_losses(intelligence_db):
    from database.database import connect
    from services.market_intelligence import MarketIntelligenceEngine
    from services.market_intelligence_repository import MarketIntelligenceRepository
    from services.research_engine import ResearchEngine

    repo = MarketIntelligenceRepository()
    for index, (quality_override, realized_r) in enumerate(((80, 2), (65, -1), (40, 1), (35, -1)), 1):
        signal_id = 5100 + index
        research = _research_row(signal_id, f"2026-03-{index:02d}T00:00:00+00:00")
        _insert_research_parent(research)
        intelligence = MarketIntelligenceEngine().analyze_timeframe(
            _frame(), timeframe="1h", side="LONG", plan=_plan())
        intelligence["signal_quality_v4"]["overall_quality"] = quality_override
        repo.persist_signal(research, {"market_intelligence": intelligence})
        outcome = {"pure_market": {"eligible": True, "signal_r": realized_r}}
        with connect() as conn:
            conn.execute("""INSERT INTO research_outcomes(snapshot_id,signal_id,outcome_checksum,
                outcome_version,signal_result,signal_r,mfe_pct,mae_pct,tp_progression_json,stop_reached,
                policy_outcomes_json,execution_outcomes_json,manual_intervention,no_intervention_r,
                outcome_json,resolved_at,attached_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                research["snapshot_id"], signal_id, f"outcome-{signal_id}", 1,
                "TP3" if realized_r > 0 else "STOP", realized_r, 1, -1, "{}",
                int(realized_r < 0), "[]", "[]", 0, None, json.dumps(outcome),
                research["decision_at"], research["decision_at"]))
    threshold = next(item for item in repo.quality_threshold_report(88)["threshold_curves"]
                     if item["threshold"] == 70)
    assert threshold["trades"] == 1 and threshold["missed_winners"] == 1
    assert threshold["avoided_losses"] == 2
    rankings = ResearchEngine().rankings(88)
    assert rankings and rankings[0]["rank_version"] == "signal-ranking-v5"
    assert rankings[0]["components"]["strongest_advantages"]
    assert rankings[0]["components"]["strongest_weaknesses"]


def test_replayed_capture_projects_only_immutable_snapshot_features(intelligence_db):
    from database.database import connect
    from services.market_intelligence import MarketIntelligenceEngine
    from services.market_intelligence_repository import MarketIntelligenceRepository
    from services.research_engine import ResearchEngine

    now = "2026-04-01T00:00:00+00:00"
    first = MarketIntelligenceEngine().analyze_timeframe(
        _frame(), timeframe="1h", side="LONG", plan=_plan())
    second = MarketIntelligenceEngine().analyze_timeframe(
        _frame(start=120, end=90), timeframe="1h", side="SHORT", plan=_plan("SHORT"))
    second["market_story"]["state"] = "MUTATED_CURRENT_STATE"
    with connect() as conn:
        conn.execute("""INSERT INTO signals(id,owner_telegram_id,symbol,timeframe,side,status,
            created_at,updated_at,entry,stop,tp1,tp2,tp3,rr,confidence,bull_score,bear_score,
            recommendation,setup_key,features_json,reasons_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            5150, 88, "BTCUSDT", "1h", "LONG", "ACTIVE", now, now, 120, 116, 124,
            128, 132, 3, 75, 75, 25, "READY", "immutable", json.dumps({"market_intelligence": first}), "[]"))
    engine = ResearchEngine()
    stored_snapshot = engine.capture_signal(5150)
    original_state = first["market_story"]["state"]
    with connect() as conn:
        conn.execute("DELETE FROM market_intelligence_snapshots WHERE signal_id=5150")
        conn.execute("DELETE FROM research_signal_rankings WHERE snapshot_id=? AND rank_version='signal-ranking-v5'",
                     (stored_snapshot["snapshot_id"],))
        conn.execute("UPDATE signals SET features_json=? WHERE id=5150",
                     (json.dumps({"market_intelligence": second}),))
    engine.capture_signal(5150)
    replayed = MarketIntelligenceRepository().get_signal(5150, 88)
    assert replayed["story"]["state"] == original_state
    assert replayed["story"]["state"] != "MUTATED_CURRENT_STATE"


@pytest.mark.asyncio
async def test_bingx_public_research_endpoints_are_exact_and_bounded():
    from services.exchanges.bingx_swap import BingXSwapAdapter
    from services.exchanges.models import ExchangeCredentials

    adapter = BingXSwapAdapter(ExchangeCredentials("", "", False))
    adapter._request = AsyncMock(side_effect=[
        {"bids": [["100", "2"]], "asks": [["101", "3"]], "T": 123},
        {"lastFundingRate": "0.0001", "markPrice": "100"},
        {"openInterest": "12345"},
    ])
    depth = await adapter.market_depth("BTCUSDT", limit=500)
    context = await adapter.funding_open_interest("BTCUSDT")
    assert len(depth["bids"]) == 1 and context["funding_rate"] == "0.0001"
    calls = adapter._request.await_args_list
    assert calls[0].args[0] == "/openApi/swap/v2/quote/depth"
    assert calls[0].kwargs["params"] == {"symbol": "BTC-USDT", "limit": 100}
    assert {calls[1].args[0], calls[2].args[0]} == {
        "/openApi/swap/v2/quote/premiumIndex", "/openApi/swap/v2/quote/openInterest"}


def test_ai_red_team_context_is_bounded_and_contains_no_outcome(intelligence_db):
    from database.database import connect
    from services.ai_trading import AIContextBuilder, PROMPT_VERSION, SYSTEM_PROMPT
    from services.market_intelligence import MarketIntelligenceEngine
    from services.market_intelligence_repository import MarketIntelligenceRepository

    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("""INSERT INTO signals(id,owner_telegram_id,symbol,timeframe,side,status,
            created_at,updated_at,entry,stop,tp1,tp2,tp3,rr,confidence,bull_score,bear_score,
            recommendation,setup_key,features_json,reasons_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            5201, 88, "BTCUSDT", "1h", "LONG", "ACTIVE", now, now, 120, 116, 124,
            128, 132, 3, 75, 75, 25, "READY", "test", "{}", "[]"))
    intelligence = MarketIntelligenceEngine().analyze_timeframe(
        _frame(), timeframe="1h", side="LONG", plan=_plan())
    MarketIntelligenceRepository().persist_signal(_research_row(5201, now),
                                                   {"market_intelligence": intelligence})
    payload = AIContextBuilder().from_signal(5201, telegram_id=88).prompt_payload()
    serialized = json.dumps(payload).lower()
    assert "market_intelligence_v2" in payload["features"]
    assert "realized_r" not in serialized and "outcome" not in serialized
    assert PROMPT_VERSION.startswith("ai-red-team")
    assert "weakest evidence" in SYSTEM_PROMPT and "chain-of-thought" in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_disabled_microstructure_worker_is_bounded_and_side_effect_free(intelligence_db, monkeypatch):
    from database.database import connect
    from services.microstructure_observer import MicrostructureObserver

    monkeypatch.setenv("MICROSTRUCTURE_COLLECTION_ENABLED", "false")
    monkeypatch.setenv("MICROSTRUCTURE_MAX_SYMBOLS", "invalid")
    worker = MicrostructureObserver(interval_seconds=30)
    assert worker.max_symbols == 8
    result = await worker.check_once()
    assert result == {"skipped": True, "reason": "DISABLED", "symbols": 0, "persisted": 0, "errors": 0}
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM microstructure_aggregates").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM paper_execution_orders").fetchone()["n"] == 0


@pytest.mark.asyncio
async def test_public_observer_client_has_no_credentials_and_constructor_failure_releases_lease(
        intelligence_db, monkeypatch):
    from database.database import connect
    import services.microstructure_observer as observer_module

    public = observer_module._public_bingx_adapter()
    assert public.configured is False
    assert public.credentials.api_key == "" and public.credentials.api_secret == ""
    assert public.environment == "prod-live"
    await public.close()

    monkeypatch.setenv("MICROSTRUCTURE_COLLECTION_ENABLED", "true")
    monkeypatch.setattr(observer_module.MicrostructureObserver, "_symbols", lambda self: [])
    monkeypatch.setattr(
        observer_module,
        "_public_bingx_adapter",
        lambda: (_ for _ in ()).throw(RuntimeError("PUBLIC_CLIENT_CONSTRUCTION_FAILED")),
    )
    worker = observer_module.MicrostructureObserver(interval_seconds=30)
    with pytest.raises(RuntimeError, match="PUBLIC_CLIENT_CONSTRUCTION_FAILED"):
        await worker.check_once()
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) n FROM distributed_leases WHERE lease_name=?",
            (worker.worker_name,),
        ).fetchone()["n"] == 0
        state = conn.execute(
            "SELECT last_error FROM runtime_state WHERE worker_name=?",
            (worker.worker_name,),
        ).fetchone()
    assert state and "PUBLIC_CLIENT_CONSTRUCTION_FAILED" in state["last_error"]


@pytest.mark.asyncio
async def test_enabled_microstructure_worker_bounds_symbols_samples_and_persistence(intelligence_db, monkeypatch):
    from database.database import connect
    import services.microstructure_observer as observer_module

    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        for signal_id, symbol in ((5301, "BTCUSDT"), (5302, "ETHUSDT")):
            conn.execute("""INSERT INTO signals(id,owner_telegram_id,symbol,timeframe,side,status,
                created_at,updated_at,entry,stop,tp1,tp2,tp3,rr,confidence,bull_score,bear_score,
                recommendation,setup_key,features_json,reasons_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                signal_id, 88, symbol, "1h", "LONG", "ACTIVE", now, now, 100, 95, 102,
                104, 106, 2, 70, 70, 30, "READY", "test", "{}", "[]"))

    class FakeAdapter:
        environment = "prod-live"

        def __init__(self):
            self.depth_calls = 0

        async def market_depth(self, symbol, limit):
            self.depth_calls += 1
            return {"bids": [[99.9, 10], [99.8, 2]],
                    "asks": [[100.1, 8], [100.2, 2]], "timestamp": self.depth_calls}

        async def funding_open_interest(self, symbol):
            return {"funding_rate": "0.0001", "open_interest": "1000"}

        async def close(self):
            return None

    adapter = FakeAdapter()

    monkeypatch.setattr(observer_module, "_public_bingx_adapter", lambda: adapter)
    monkeypatch.setenv("MICROSTRUCTURE_COLLECTION_ENABLED", "true")
    monkeypatch.setenv("MICROSTRUCTURE_MAX_SYMBOLS", "1")
    monkeypatch.setenv("MICROSTRUCTURE_SAMPLES_PER_SYMBOL", "3")
    monkeypatch.setenv("MICROSTRUCTURE_SAMPLE_SPACING_MS", "100")
    worker = observer_module.MicrostructureObserver(interval_seconds=30)
    assert worker._lease_ttl() >= 180
    result = await worker.check_once()
    assert result["symbols"] == 1 and result["persisted"] == 1 and result["errors"] == 0
    assert adapter.depth_calls == 3
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM microstructure_aggregates").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) n FROM paper_execution_orders").fetchone()["n"] == 0


@pytest.mark.asyncio
async def test_derivatives_outage_does_not_discard_valid_depth_aggregate(intelligence_db, monkeypatch):
    from database.database import connect
    import services.microstructure_observer as observer_module

    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("""INSERT INTO signals(id,owner_telegram_id,symbol,timeframe,side,status,
            created_at,updated_at,entry,stop,tp1,tp2,tp3,rr,confidence,bull_score,bear_score,
            recommendation,setup_key,features_json,reasons_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            5303, 88, "BTCUSDT", "1h", "LONG", "ACTIVE", now, now, 100, 95, 102,
            104, 106, 2, 70, 70, 30, "READY", "test", "{}", "[]"))

    class PartialAdapter:
        environment = "prod-live"

        async def market_depth(self, symbol, limit):
            return {"bids": [[99.9, 10]], "asks": [[100.1, 8]], "timestamp": 123}

        async def funding_open_interest(self, symbol):
            raise RuntimeError("public derivatives endpoint unavailable")

        async def close(self):
            return None

    monkeypatch.setattr(observer_module, "_public_bingx_adapter", PartialAdapter)
    monkeypatch.setenv("MICROSTRUCTURE_COLLECTION_ENABLED", "true")
    monkeypatch.setenv("MICROSTRUCTURE_MAX_SYMBOLS", "1")
    monkeypatch.setenv("MICROSTRUCTURE_SAMPLES_PER_SYMBOL", "3")
    monkeypatch.setenv("MICROSTRUCTURE_SAMPLE_SPACING_MS", "100")
    result = await observer_module.MicrostructureObserver(interval_seconds=30).check_once()

    assert result["persisted"] == 1 and result["errors"] == 2
    assert result["state"] == "DEGRADED"
    row = observer_module.MarketIntelligenceRepository().latest_microstructure("BTCUSDT")
    assert row["aggregate"]["status"] == "AVAILABLE"
    assert row["aggregate"]["funding_open_interest"]["status"] == "UNAVAILABLE"
    assert row["aggregate"]["raw_book_persisted"] is False

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def edge_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "edge.db")
    monkeypatch.setenv("EDGE_MIN_SAMPLES", "3")
    monkeypatch.setenv("EDGE_MODERATE_SAMPLES", "5")
    monkeypatch.setenv("EDGE_HIGH_SAMPLES", "10")
    monkeypatch.setenv("EDGE_COMBINATION_MIN_SAMPLES", "3")
    monkeypatch.setenv("EDGE_COMBINATION_MIN_SUPPORT", "0.05")
    monkeypatch.setenv("EDGE_MAX_COMBINATIONS", "120")
    monkeypatch.setenv("EDGE_BOOTSTRAP_SAMPLES", "200")
    monkeypatch.setenv("EDGE_FORWARD_MIN_SAMPLES", "3")
    monkeypatch.setenv("EDGE_WALK_FORWARD_MIN_TRAIN", "6")
    monkeypatch.setenv("EDGE_WALK_FORWARD_VALIDATION_SIZE", "3")
    monkeypatch.setenv("SCALPING_MIN_SAMPLES", "3")
    monkeypatch.setenv("SCALPING_TAKER_FEE_PCT", "0.05")
    monkeypatch.setenv("SCALPING_SPREAD_PCT", "0.02")
    monkeypatch.setenv("SCALPING_SLIPPAGE_PCT", "0.03")
    monkeypatch.setenv("SCALPING_LATENCY_PENALTY_PCT", "0.01")
    from database.database import create_tables
    create_tables()
    create_tables()
    return tmp_path


def _resolved(signal_id: int, *, decision_at: datetime, result_r: float,
              bos: bool, sweep: bool, fvg: bool = True, timeframe: str = "5m",
              owner: int = 77, manual: bool = False):
    from database.database import connect
    from services.research_engine import ResearchEngine

    created = decision_at.astimezone(timezone.utc).isoformat()
    features = {
        "market_regime": "trend breakout", "trend": "bullish up",
        "bos": bos, "sweep": sweep, "fvg": fvg, "htf_alignment": True,
        "structural_strength": 75, "rsi": 58, "momentum_score": 70,
        "atr_pct": 1.2, "ema50": 101, "ema200": 98, "ema_slope": .4,
    }
    with connect() as conn:
        conn.execute("""INSERT INTO signals(id,owner_telegram_id,symbol,timeframe,side,status,
            created_at,updated_at,activated_at,entry,stop,tp1,tp2,tp3,rr,confidence,
            bull_score,bear_score,recommendation,setup_key,features_json,reasons_json,current_price,
            max_profit_pct,max_drawdown_pct)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            signal_id, owner, "BTCUSDT", timeframe, "LONG", "ACTIVE", created, created, created,
            100, 99, 101, 102, 103, 3, 75, 75, 25, "READY", "liquidity-breakout",
            json.dumps(features), "[]", 100, 0, 0))
    engine = ResearchEngine()
    snapshot = engine.capture_signal(signal_id)
    status = "MANUAL_STOP" if manual else "TP3" if result_r > 0 else "STOP"
    closed = (decision_at + timedelta(hours=1)).isoformat()
    with connect() as conn:
        conn.execute("""UPDATE signals SET status=?,result=?,realized_r=?,closed_at=?,updated_at=?,
            max_profit_pct=?,max_drawdown_pct=? WHERE id=?""", (
            status, status, result_r, closed, closed,
            3.2 if result_r > 0 else .4, -.5 if result_r > 0 else -1.2, signal_id))
    assert engine.attach_outcome(snapshot)
    return snapshot


def _dataset(start_id: int = 3000, count: int = 12, *, start: datetime | None = None):
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(count):
        positive = index % 2 == 0
        _resolved(start_id + index, decision_at=start + timedelta(hours=index),
                  result_r=2 if positive else -1, bos=positive, sweep=positive)


def test_feature_normalization_is_immutable_explicit_and_future_safe(edge_db):
    from database.database import connect
    from services.edge_discovery import EdgeDiscoveryEngine, FEATURE_VERSION

    snapshot = _resolved(2901, decision_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                         result_r=2, bos=True, sweep=True)
    engine = EdgeDiscoveryEngine()
    assert engine.normalize_pending() == 1
    with connect() as conn:
        original = conn.execute("SELECT * FROM research_feature_vectors WHERE snapshot_id=?",
                                (snapshot["snapshot_id"],)).fetchone()
        conn.execute("UPDATE signals SET features_json=?,realized_r=-99 WHERE id=2901",
                     ('{"bos":false,"future_price":0}',))
    assert engine.normalize_pending() == 0
    with connect() as conn:
        stored = conn.execute("SELECT * FROM research_feature_vectors WHERE snapshot_id=?",
                              (snapshot["snapshot_id"],)).fetchone()
    vector = json.loads(stored["vector_json"])
    missing = json.loads(stored["missing_features_json"])
    assert stored["feature_version"] == FEATURE_VERSION
    assert stored["data_quality"] == "TRUSTWORTHY_DECISION_TIME"
    assert stored["vector_checksum"] == original["vector_checksum"]
    assert vector["bos"] is True and "future_price" not in vector
    assert "macd" in missing


def test_outcome_layers_keep_pure_market_separate_from_human_intervention(edge_db):
    from database.database import connect
    from services.edge_discovery import EdgeDiscoveryEngine

    pure = _resolved(2910, decision_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     result_r=2, bos=True, sweep=True)
    manual = _resolved(2911, decision_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                       result_r=-.2, bos=True, sweep=True, manual=True)
    EdgeDiscoveryEngine().normalize_pending()
    with connect() as conn:
        rows = conn.execute("SELECT snapshot_id,outcome_json FROM research_outcomes ORDER BY id").fetchall()
    outcomes = {row["snapshot_id"]: json.loads(row["outcome_json"]) for row in rows}
    assert outcomes[pure["snapshot_id"]]["pure_market"]["eligible"] is True
    assert outcomes[pure["snapshot_id"]]["pure_market"]["max_favorable_r"] == pytest.approx(3.2)
    assert outcomes[pure["snapshot_id"]]["deterministic_policy"]
    assert outcomes[pure["snapshot_id"]]["execution"]
    assert outcomes[manual["snapshot_id"]]["pure_market"]["eligible"] is False
    observations = EdgeDiscoveryEngine.observations(77)
    assert [row["signal_id"] for row in observations] == [2910]


def test_data_quality_quarantines_late_and_contaminated_snapshots(edge_db):
    from database.database import connect
    from services.edge_discovery import EdgeDiscoveryEngine

    late = _resolved(2920, decision_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     result_r=2, bos=True, sweep=True)
    contaminated = _resolved(2921, decision_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                             result_r=2, bos=True, sweep=True)
    with connect() as conn:
        conn.execute("UPDATE research_signal_snapshots SET capture_quality='LATE_TERMINAL_BACKFILL' WHERE snapshot_id=?",
                     (late["snapshot_id"],))
        row = conn.execute("SELECT snapshot_json FROM research_signal_snapshots WHERE snapshot_id=?",
                           (contaminated["snapshot_id"],)).fetchone()
        payload = json.loads(row["snapshot_json"])
        payload["realized_r"] = 99
        conn.execute("UPDATE research_signal_snapshots SET snapshot_json=? WHERE snapshot_id=?",
                     (json.dumps(payload), contaminated["snapshot_id"]))
    engine = EdgeDiscoveryEngine()
    assert engine.normalize_pending() == 2
    quality = engine.data_quality_report(77)
    assert quality["counts"]["LATE_BACKFILL"] == 1
    assert quality["counts"]["CONTAMINATED"] == 1
    assert engine.observations(77) == []


def test_statistics_feature_contribution_combinations_and_negative_candidates(edge_db):
    from services.edge_discovery import EdgeDiscoveryEngine, StatisticalResearch

    _dataset()
    engine = EdgeDiscoveryEngine()
    assert engine.normalize_pending() == 12
    observations = engine.observations(77)
    first = StatisticalResearch.metrics(observations, seed_key="stable")
    second = StatisticalResearch.metrics(observations, seed_key="stable")
    assert first["expectancy_interval_95"] == second["expectancy_interval_95"]
    assert first["sample_tier"] == "HIGH" and first["sample_size"] == 12
    bos = next(item for item in engine.feature_contributions(77)["features"] if item["feature"] == "bos")
    assert bos["expectancy_delta_r"] == pytest.approx(3)
    assert bos["control"] == "TIMEFRAME_REGIME_DIRECTION_STRATIFIED"
    combos = engine.combination_mining(77)
    bos_sweep = next(item for item in combos["findings"]
                     if item["features"] == ["bos", "liquidity_sweep"])
    assert bos_sweep["status"] if "status" in bos_sweep else combos["status"] == "EXPLORATORY"
    assert bos_sweep["expectancy_delta_r"] == pytest.approx(3)
    assert engine.negative_edge_candidates(77)["automatic_production_exclusion"] is False


def test_walk_forward_is_chronological_and_persists_provenance(edge_db):
    from database.database import connect
    from services.edge_discovery import EdgeDiscoveryEngine, MODEL_VERSION

    _dataset(start_id=3100, count=15)
    engine = EdgeDiscoveryEngine()
    engine.normalize_pending()
    report = engine.walk_forward(77, persist=True)
    assert report["status"] == "EXPLORATORY" and report["random_split_used"] is False
    assert report["folds"]
    for fold in report["folds"]:
        assert fold["train_cutoff"] < fold["validation_start"]
    with connect() as conn:
        run = conn.execute("SELECT * FROM research_model_runs ORDER BY id LIMIT 1").fetchone()
    provenance = json.loads(run["provenance_json"])
    assert run["model_version"] == MODEL_VERSION
    assert provenance["chronological_split"] is True and provenance["random_split"] is False


def test_cohorts_confidence_regime_specialization_and_exit_uncertainty(edge_db):
    from services.edge_discovery import EdgeDiscoveryEngine

    _dataset(start_id=3150, count=12)
    engine = EdgeDiscoveryEngine()
    engine.normalize_pending()
    cohorts = engine.cohort_edges(77)
    regimes = {item["cohort"] for item in cohorts["dimensions"]["regime"]}
    calibration = engine.confidence_calibration(77)
    strategy_regimes = engine.strategy_regime_report(77)
    exits = engine.exit_research(77)
    assert {"TREND_UP", "BREAKOUT"}.issubset(regimes)
    assert calibration["buckets"][0]["bucket"] == "75-80"
    assert any(item["strategy"] == "NAIVE_ELIGIBLE" for item in strategy_regimes["results"])
    unavailable = {item["status"] for item in exits["policies"] if item["policy"] == "TRAILING"}
    assert unavailable == {"INSUFFICIENT_ORDERED_PATH_DATA"}


def test_hypothesis_definition_freezes_and_only_future_rows_validate(edge_db):
    from database.database import connect
    from services.edge_discovery import EdgeDiscoveryEngine

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _dataset(start_id=3200, count=12, start=start)
    engine = EdgeDiscoveryEngine()
    engine.normalize_pending()
    refresh = engine.refresh_findings(77)
    assert refresh["hypotheses_frozen"] > 0
    with connect() as conn:
        hypothesis = conn.execute("""SELECT * FROM research_hypotheses
            WHERE filter_json LIKE '%bos%' ORDER BY id LIMIT 1""").fetchone()
    assert hypothesis and hypothesis["lifecycle_state"] == "FORWARD_TESTING"
    assert json.loads(hypothesis["lifecycle_history_json"]) == [
        "DISCOVERED", "BACKTESTED", "FORWARD_TESTING"]
    original_filter = hypothesis["filter_json"]
    future_start = datetime.fromisoformat(hypothesis["forward_start_at"]) + timedelta(hours=1)
    for index in range(10):
        positive = index < 5
        _resolved(3300 + index, decision_at=future_start + timedelta(hours=index),
                  result_r=2 if positive else -1, bos=positive, sweep=positive)
    engine.normalize_pending()
    assert engine.evaluate_forward_hypotheses(77) > 0
    with connect() as conn:
        updated = conn.execute("SELECT * FROM research_hypotheses WHERE hypothesis_id=?",
                               (hypothesis["hypothesis_id"],)).fetchone()
        evaluation = conn.execute("""SELECT * FROM research_hypothesis_evaluations
            WHERE hypothesis_id=? ORDER BY id DESC LIMIT 1""", (hypothesis["hypothesis_id"],)).fetchone()
    assert updated["filter_json"] == original_filter
    assert evaluation["sample_size"] == 5 and evaluation["baseline_sample_size"] == 5
    assert updated["lifecycle_state"] == "CONFIRMED"
    assert json.loads(updated["lifecycle_history_json"])[-1] == "CONFIRMED"


def test_rr_scalping_selector_ranking_and_portfolio_remain_shadow_only(edge_db):
    from database.database import connect
    from services.edge_discovery import EdgeDiscoveryEngine, RANK_VERSION, SELECTOR_VERSION

    _dataset(start_id=3400, count=12)
    for index in range(6):
        _resolved(3500 + index, decision_at=datetime(2026, 2, 1, tzinfo=timezone.utc) + timedelta(hours=index),
                  result_r=2 if index % 2 == 0 else -1, bos=index % 2 == 0,
                  sweep=index % 2 == 0, timeframe="1m")
    engine = EdgeDiscoveryEngine()
    engine.normalize_pending()
    assert engine.refresh_strategy_selector() > 0
    assert engine.refresh_rankings() > 0
    rr = engine.rr_research(77)
    scalp = engine.scalping_lab(77)
    portfolio = engine.portfolio_edge(77)
    assert rr["mode"] == "SHADOW_RESEARCH_ONLY"
    assert scalp["mode"] == "PAPER_SHADOW_ONLY" and scalp["roundtrip_cost_pct"] == pytest.approx(.19)
    assert portfolio["automatic_portfolio_optimization"] is False
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM research_signal_rankings WHERE rank_version=?",
                            (RANK_VERSION,)).fetchone()["n"] > 0
        assert conn.execute("SELECT COUNT(*) n FROM research_strategy_recommendations WHERE selector_version=?",
                            (SELECTOR_VERSION,)).fetchone()["n"] > 0
        assert conn.execute("SELECT COUNT(*) n FROM paper_execution_orders").fetchone()["n"] == 0


def test_research_export_separates_decision_features_from_later_outcomes(edge_db):
    from services.alpha_research import AlphaResearchEngine
    from services.edge_discovery import EdgeDiscoveryEngine
    from tools.export_alpha_dataset import load_research_rows

    _resolved(3550, decision_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
              result_r=2, bos=True, sweep=True)
    EdgeDiscoveryEngine().normalize_pending()
    row = AlphaResearchEngine().feature_row(load_research_rows()[0])
    decision = json.loads(row["decision_features_json"])
    outcome = json.loads(row["later_outcome_json"])
    assert decision["bos"] is True and "signal_r" not in decision
    assert outcome["pure_market"]["signal_r"] == 2
    assert row["normalized_data_quality"] == "TRUSTWORTHY_DECISION_TIME"


@pytest.mark.asyncio
async def test_edge_worker_is_restart_safe_and_edge_failure_isolated(edge_db, monkeypatch):
    from services.research_worker import ResearchWorker

    _resolved(3600, decision_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
              result_r=2, bos=True, sweep=True)
    worker = ResearchWorker(interval_seconds=60)
    first = await worker.check_once()
    second = await worker.check_once()
    assert first["normalized"] == 1 and second["normalized"] == 0

    def fail(_limit):
        raise RuntimeError("research projection failure")

    monkeypatch.setattr(worker.edge_engine, "run_cycle", fail)
    isolated = await worker.check_once()
    assert isolated["edge_errors"] == 1 and isolated["captured"] == 0

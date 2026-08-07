from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


@pytest.fixture()
def research_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "research.db")
    monkeypatch.setenv("RESEARCH_MIN_SAMPLES", "3")
    monkeypatch.setenv("SCALPING_TAKER_FEE_PCT", "0.05")
    monkeypatch.setenv("SCALPING_SPREAD_PCT", "0.02")
    monkeypatch.setenv("SCALPING_SLIPPAGE_PCT", "0.03")
    monkeypatch.setenv("SCALPING_LATENCY_PENALTY_PCT", "0.01")
    from database.database import create_tables
    create_tables()


def _insert_signal(signal_id: int, *, owner: int = 901, timeframe: str = "5m",
                   status: str = "ACTIVE", realized_r=None, features=None):
    from database.database import connect
    now = datetime.now(timezone.utc).isoformat()
    payload = features or {
        "market_regime": "trend breakout compression",
        "trend": "bullish up", "volatility_state": "high",
        "bos": "bullish BOS", "sweep": True, "fvg": {"present": True},
        "rsi": 34, "atr_pct": 3,
    }
    closed = now if status in {"TP3", "STOP", "MANUAL_STOP"} else None
    with connect() as conn:
        conn.execute("""INSERT INTO signals(id,owner_telegram_id,symbol,timeframe,side,status,
            created_at,updated_at,activated_at,closed_at,entry,stop,tp1,tp2,tp3,rr,confidence,
            bull_score,bear_score,recommendation,setup_key,features_json,reasons_json,current_price,
            max_profit_pct,max_drawdown_pct,realized_r,result)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            signal_id, owner, "BTCUSDT", timeframe, "LONG", status, now, now, now, closed,
            100, 99 if timeframe in {"1m", "3m", "5m"} else 95, 102, 103, 104, 3, 82,
            82, 18, "READY", "liquidity-breakout", json.dumps(payload), "[]", 100,
            4, -1.5, realized_r, status if closed else None,
        ))


def test_snapshot_is_immutable_future_safe_and_runs_shadow_strategies(research_db):
    from database.database import connect
    from services.research_engine import ResearchEngine
    _insert_signal(1801)
    engine = ResearchEngine()
    first = engine.capture_signal(1801)
    original = first["snapshot_json"]
    snapshot = json.loads(original)
    assert first["capture_quality"] == "DECISION_TIME"
    assert snapshot["regimes"] == ["TREND_UP", "COMPRESSION", "BREAKOUT", "HIGH_VOLATILITY"]
    assert "result" not in snapshot and "realized_r" not in snapshot and "closed_at" not in snapshot
    with connect() as conn:
        conn.execute("""UPDATE signals SET features_json='{"market_regime":"range"}',
            status='TP3',result='TP3',realized_r=3,closed_at=?,max_profit_pct=7 WHERE id=1801""",
            (datetime.now(timezone.utc).isoformat(),))
    second = engine.capture_signal(1801)
    assert second["snapshot_json"] == original
    assert engine.attach_outcome(second)
    assert not engine.attach_outcome(second)
    with connect() as conn:
        decisions = conn.execute(
            "SELECT strategy_key,action FROM research_strategy_decisions WHERE signal_id=1801 ORDER BY strategy_key"
        ).fetchall()
        rankings = conn.execute("SELECT COUNT(*) n FROM research_signal_rankings").fetchone()["n"]
        orders = conn.execute("SELECT COUNT(*) n FROM paper_execution_orders").fetchone()["n"]
        outcome = conn.execute("SELECT * FROM research_outcomes WHERE signal_id=1801").fetchone()
    assert {row["strategy_key"] for row in decisions} == {
        "NAIVE_ELIGIBLE", "LIQUIDITY_SMC", "TREND_FOLLOWING", "BREAKOUT", "MEAN_REVERSION"
    }
    assert rankings == 1 and orders == 0
    assert outcome["signal_r"] == 3 and outcome["mfe_pct"] == 7


def test_late_backfill_is_labeled_and_manual_outcome_excluded(research_db):
    from services.research_engine import ResearchEngine
    _insert_signal(1802, status="TP3", realized_r=2)
    _insert_signal(1803, status="MANUAL_STOP", realized_r=-0.4)
    engine = ResearchEngine()
    late = engine.capture_signal(1802)
    manual = engine.capture_signal(1803)
    assert late["capture_quality"] == "LATE_TERMINAL_BACKFILL"
    assert manual["capture_quality"] == "LATE_TERMINAL_BACKFILL"
    engine.attach_outcome(late)
    engine.attach_outcome(manual)
    report = engine.cohort_report(901, minimum_samples=2)
    assert report["overall"]["sample_size"] == 0
    assert report["overall"]["manual_excluded"] == 1
    assert report["overall"]["late_backfill_excluded"] == 2
    assert report["overall"]["status"] == "INSUFFICIENT_SAMPLES"


def test_strategy_comparison_cohorts_and_scalping_costs(research_db):
    from services.research_engine import ResearchEngine
    engine = ResearchEngine()
    for signal_id, result in ((1810, 2), (1811, -1), (1812, 1)):
        _insert_signal(signal_id, timeframe="1m")
        snapshot = engine.capture_signal(signal_id)
        from database.database import connect
        with connect() as conn:
            conn.execute("UPDATE signals SET status=?,result=?,realized_r=?,closed_at=? WHERE id=?", (
                "TP3" if result > 0 else "STOP", "TP3" if result > 0 else "STOP", result,
                datetime.now(timezone.utc).isoformat(), signal_id,
            ))
        assert engine.attach_outcome(snapshot)
    cohorts = engine.cohort_report(901)
    assert cohorts["overall"]["sample_size"] == 3
    assert cohorts["overall"]["expectancy_r"] == pytest.approx(2 / 3)
    comparison = engine.strategy_comparison(901)
    liquidity = next(item for item in comparison["strategies"] if item["strategy"] == "LIQUIDITY_SMC")
    assert liquidity["identical_resolved_snapshots"] == 3
    assert liquidity["expectancy_r"] == pytest.approx(2 / 3)
    scalping = engine.scalping_report(901)
    assert scalping["roundtrip_cost_pct"] == pytest.approx(.19)
    assert scalping["timeframes"]["1m"]["after_cost_expectancy_r"] == pytest.approx((2 - .19 - 1 - .19 + 1 - .19) / 3)
    assert not scalping["timeframes"]["1m"]["positive_after_cost"]


def test_capability_boundary_uses_central_entitlement_without_execution_authority(research_db):
    from services.capabilities import CapabilityService
    service = CapabilityService()
    initial = service.snapshot(901)
    assert initial["STRATEGY_LAB"]["enabled"]
    updated = service.set_entitlement(901, "strategy_lab", enabled=False, source="TEST")
    assert not updated["enabled"] and updated["source"] == "TEST"
    assert not updated["economic_authority"]


def test_legacy_exporter_uses_immutable_snapshot_rows_when_available(research_db):
    from services.alpha_research import AlphaResearchEngine
    from services.research_engine import ResearchEngine
    _insert_signal(1819)
    snapshot = ResearchEngine().capture_signal(1819)
    row = AlphaResearchEngine().feature_row({**snapshot, "outcome_json": "{}"})
    assert row["snapshot_safety"] == "IMMUTABLE_DECISION_SNAPSHOT"
    assert row["capture_quality"] == "DECISION_TIME"
    legacy = AlphaResearchEngine().feature_row({"id": 1, "features_json": "{}"})
    assert legacy["snapshot_safety"] == "NOT_VERIFIED_FUTURE_SAFE"


@pytest.mark.asyncio
async def test_research_worker_is_lease_protected_and_bounded(research_db, monkeypatch):
    from services.research_worker import ResearchWorker
    _insert_signal(1820)
    monkeypatch.setenv("RESEARCH_BATCH_LIMIT", "10")
    worker = ResearchWorker(interval_seconds=60)
    result = await worker.check_once()
    assert not result["skipped"] and result["captured"] == 1
    replay = await worker.check_once()
    assert replay["captured"] == 0

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from services.copy_execution_planner import CopyExecutionPlanner
from services.decision_quality import DecisionQualityEngine
from services.execution_models import RiskProfile
from services.signal_recorder import SignalRecorder


def _candidate(**updates):
    candidate = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "direction": "LONG",
        "direction_score": 76.0,
        "setup_score": 74.0,
        "score": 74.0,
        "confidence": 80.0,
        "bull_score": 76.0,
        "bear_score": 24.0,
        "execution_status": "🟢 READY",
        "recommendation": "BUY",
        "plan_valid": True,
        "market_regime": {"code": "TRENDING", "label": "Trending"},
        "data_quality": {"status": "GOOD"},
        "trend": "🟢 Bullish",
        "structure": "🟢 Bullish BOS",
        "premium": {"zone": "🟢 Discount"},
        "reasons": ["✅ Trend aligned"],
        "triggers": ["Hold structure"],
        "score_components": [{"label": "Trend aligned", "value": 18, "group": "Trend"}],
        "confirmations": ["Trend aligned"],
        "market_bias": "BULLISH",
        "price": 100.0,
        "entry": 100.0,
        "stop": 98.0,
        "tp1": 104.0,
        "tp2": 106.0,
        "tp3": 108.0,
        "rr": 2.0,
        "entry_quality": 75.0,
        "risk_quality": 75.0,
        "execution_readiness": 75.0,
        "directional_edge": 26.0,
        "opportunity_category": "READY",
        "ranking_score": 75.0,
        "preferred_entry_low": 99.0,
        "preferred_entry_high": 101.0,
    }
    candidate.update(updates)
    return candidate


class _FakeMarket:
    async def get_klines(self, *_args, **_kwargs):
        return object()


class _FakeAnalyzer:
    def __init__(self, candidate=None):
        self.candidate = candidate or _candidate()

    def analyze(self, _frame, **_kwargs):
        return dict(self.candidate)


class _FakeProbability:
    def enrich(self, analysis, **_kwargs):
        result = dict(analysis)
        result["historical_probability"] = {"samples": 0, "reliability": "Insufficient"}
        return result


class _ObservationSpy:
    def __init__(self):
        self.rows = []
        self.promotions = []

    def save_or_update(self, **kwargs):
        self.rows.append(dict(kwargs["analysis"]))
        return len(self.rows)

    def promote(self, observation_id, signal_id):
        self.promotions.append((observation_id, signal_id))


class _HistorySpy:
    def __init__(self):
        self.payloads = []

    def get_open_market(self, *_args):
        return []

    def save(self, payload):
        self.payloads.append(payload)
        return 91

    def update_lifecycle(self, *_args, **_kwargs):
        return None


class _RecorderSpy:
    _setup_key = staticmethod(SignalRecorder._setup_key)

    def __init__(self):
        self.analyses = []

    def record(self, **kwargs):
        self.analyses.append(dict(kwargs["analysis"]))
        return 92 if kwargs["analysis"].get("decision_outcome") == "APPROVED" else None


def _engine(tmp_path):
    engine = DecisionQualityEngine()
    engine.memory.path = tmp_path / "market_memory.jsonl"
    return engine


def test_same_candidate_has_same_authoritative_semantics_for_all_sources(tmp_path):
    outcomes = []
    vetoes = []
    for source in ("MANUAL_ANALYZE", "SCANNER", "WATCH_ENGINE", "OBSERVATION_MONITOR"):
        result = _engine(tmp_path).enrich(_candidate(), source=source)
        outcomes.append(result["decision_outcome"])
        vetoes.append(result["decision_veto_reasons"])
        assert result["decision_source"] == source
        assert DecisionQualityEngine.authorization(result) == (True, "APPROVED")
    assert outcomes == ["APPROVED"] * 4
    assert vetoes == [[]] * 4


def test_no_trade_and_existing_bad_data_flags_fail_closed(tmp_path):
    no_trade = _engine(tmp_path).enrich(_candidate(direction="NO_TRADE"), source="SCANNER")
    stale = _engine(tmp_path).enrich(
        _candidate(data_quality={"status": "GOOD", "is_stale": True}), source="WATCH_ENGINE"
    )
    invalid = _engine(tmp_path).enrich(
        _candidate(market_intelligence={"data_quality": {"status": "INVALID"}}, data_quality=None),
        source="OBSERVATION_MONITOR",
    )
    assert no_trade["decision_outcome"] == "NO_TRADE"
    assert "INVALID_OR_NO_TRADE_DIRECTION" in no_trade["decision_veto_reasons"]
    assert "DATA_QUALITY_STALE" in stale["decision_veto_reasons"]
    assert "DATA_QUALITY_INVALID" in invalid["decision_veto_reasons"]
    assert all(not DecisionQualityEngine.authorization(item)[0] for item in (no_trade, stale, invalid))


def test_quality_and_hard_veto_rejections_cannot_reach_copy_execution(tmp_path):
    weak = _engine(tmp_path).enrich(
        _candidate(direction_score=61.0, setup_score=61.0), source="MANUAL_ANALYZE"
    )
    vetoed = _engine(tmp_path).enrich(
        _candidate(reasons=["⛔ Existing deterministic veto"]), source="WATCH_ENGINE"
    )
    for rejected in (weak, vetoed):
        signal = dict(rejected)
        signal.update({"id": 77, "side": rejected["direction"], "status": "ACTIVE", "current_price": 100.0})
        plan = CopyExecutionPlanner().build(
            telegram_id=1,
            signal=signal,
            profile=RiskProfile(max_notional_pct=100.0),
            balance=10_000,
        )
        assert rejected["decision_outcome"] == "NO_TRADE"
        assert not plan.approved and plan.code == "DECISION_NOT_APPROVED"


def test_signal_recorder_observes_but_never_promotes_unapproved_ai_or_research_output():
    history = _HistorySpy()
    recorder = SignalRecorder(history=history)
    observations = _ObservationSpy()
    recorder.observations = observations

    for advisory_source in ("AI", "RESEARCH"):
        advisory = _candidate(ai_authorized=True, research_authorized=True, decision_source=advisory_source)
        assert recorder.record(symbol="BTCUSDT", timeframe="1h", analysis=advisory) is None

    assert len(observations.rows) == 2
    assert not history.payloads
    assert {row["promotion_admission"]["code"] for row in observations.rows} == {
        "DECISION_AUTHORITY_MISSING"
    }


def test_signal_recorder_persists_approved_provenance_and_copy_planner_rechecks_it(tmp_path):
    history = _HistorySpy()
    recorder = SignalRecorder(history=history)
    recorder.observations = _ObservationSpy()
    recorder._capture_research = lambda _signal_id: None
    approved = _engine(tmp_path).enrich(_candidate(), source="MANUAL_ANALYZE")

    assert recorder.record(symbol="BTCUSDT", timeframe="1h", analysis=approved) == 91
    features = history.payloads[0]["features"]
    assert features["decision_authority"] == "DecisionQualityEngine"
    assert features["decision_outcome"] == "APPROVED"
    assert features["promotion_admission"]["admitted"] is True

    persisted = dict(history.payloads[0])
    persisted.update({"id": 91, "features_json": json.dumps(features), "current_price": 100.0})
    plan = CopyExecutionPlanner().build(
        telegram_id=1,
        signal=persisted,
        profile=RiskProfile(max_notional_pct=100.0),
        balance=10_000,
    )
    missing = CopyExecutionPlanner().build(
        telegram_id=1,
        signal={key: value for key, value in persisted.items() if key not in {"features", "features_json"}},
        profile=RiskProfile(max_notional_pct=100.0),
        balance=10_000,
    )
    assert plan.approved
    assert not missing.approved and missing.code == "DECISION_AUTHORITY_MISSING"


def test_manual_wiring_and_scanner_route_apply_named_authority(tmp_path):
    from services.scanner import Scanner

    manual_source = (Path(__file__).parents[1] / "handlers" / "analyze.py").read_text(encoding="utf-8")
    assert 'decision_quality.enrich(analysis, source="MANUAL_ANALYZE")' in manual_source
    manual = _engine(tmp_path).enrich(_candidate(), source="MANUAL_ANALYZE")

    scanner = Scanner()
    scanner.market = _FakeMarket()
    scanner.analyzer = _FakeAnalyzer()
    scanner.probability = _FakeProbability()
    scanner.decision_quality = _engine(tmp_path)
    scanned = asyncio.run(scanner.analyze_coin("BTCUSDT", asyncio.Semaphore(1)))

    assert manual["decision_source"] == "MANUAL_ANALYZE"
    assert scanned["analysis"]["decision_source"] == "SCANNER"
    assert manual["decision_outcome"] == scanned["analysis"]["decision_outcome"] == "APPROVED"


def test_watch_route_applies_gate_before_recorder(monkeypatch, tmp_path):
    from services.watch_engine import WatchEngine

    engine = WatchEngine(interval_seconds=60)
    engine.market = _FakeMarket()
    engine.analyzer = _FakeAnalyzer()
    engine.probability = _FakeProbability()
    engine.decision_quality = _engine(tmp_path)
    recorder = _RecorderSpy()
    engine.recorder = recorder
    monkeypatch.setattr(engine, "_save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_add_event", lambda *_args, **_kwargs: None)

    result = asyncio.run(engine._analyze_one(
        {"symbol": "BTCUSDT", "timeframe": "1h", "telegram_id": 8, "snapshot_json": None},
        asyncio.Semaphore(1),
    ))
    assert result["ok"] is True
    assert recorder.analyses[0]["decision_source"] == "WATCH_ENGINE"
    assert recorder.analyses[0]["decision_outcome"] == "APPROVED"


def test_background_observation_applies_gate_before_promotion(monkeypatch, tmp_path):
    import services.observation_monitor as monitor_module

    monitor = monitor_module.ObservationMonitor(interval_seconds=60)
    monitor.market = _FakeMarket()
    monitor.analyzer = _FakeAnalyzer()
    monitor.probability = _FakeProbability()
    monitor.decision_quality = _engine(tmp_path)
    recorder = _RecorderSpy()
    monitor.recorder = recorder
    monitor.history = type(
        "History",
        (),
        {
            "prune_stale": staticmethod(lambda **_kwargs: 0),
            "pending": staticmethod(lambda **_kwargs: [{
                "symbol": "BTCUSDT", "timeframe": "1h", "owner_telegram_id": 9,
                "notification_chat_id": None,
            }]),
        },
    )()
    monkeypatch.setattr(monitor_module, "acquire_lease", lambda *_args: True)
    monkeypatch.setattr(monitor_module, "release_lease", lambda *_args: None)
    monkeypatch.setattr(monitor_module, "runtime_started", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(monitor_module, "runtime_finished", lambda *_args, **_kwargs: None)

    result = asyncio.run(monitor.check_once())
    assert result["promoted"] == 1
    assert recorder.analyses[0]["decision_source"] == "OBSERVATION_MONITOR"
    assert recorder.analyses[0]["decision_outcome"] == "APPROVED"

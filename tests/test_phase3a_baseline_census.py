from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from services.baseline_edge_census import (
    BaselineCensusRunner, DatasetStore, FrozenBaselineConfig, compare_baselines,
    confidence_calibration, forensic_label,
)
from services.research_replay import ReplayConfig, ReplayOutcome


def _frame(count: int = 230) -> pd.DataFrame:
    times = pd.date_range(datetime(2025, 1, 1, tzinfo=timezone.utc), periods=count, freq="h", tz="UTC")
    prices = [100 + index * .1 for index in range(count)]
    return pd.DataFrame({"time": times, "open": prices, "high": [x + 1 for x in prices],
                         "low": [x - 1 for x in prices], "close": prices, "volume": [10.0] * count,
                         "confirm": ["1"] * count})


class _Analyzer:
    def __init__(self):
        self.snapshots = []

    def analyze(self, frame, **kwargs):
        self.snapshots.append(frame.copy())
        price = float(frame.iloc[-1]["close"])
        return {"direction": "LONG", "entry": price, "stop": price - 2, "tp1": price + 2,
                "tp2": price + 4, "tp3": price + 6, "rr": 3, "entry_type": "MARKET_READY",
                "setup_score": 75, "confidence": 75, "probability": 75, "plan_valid": True,
                "execution_status": "🟢 READY", "market_regime": {"code": "TRENDING"},
                "data_quality": {"status": "VALID"}, "structure": "Bullish", "choch": "Bullish",
                "sweep": "None", "order_block": "Bullish", "premium": {"zone": "Discount"}}


class _DecisionQuality:
    def enrich(self, data, *, source=None):
        data = dict(data)
        data.update({"decision_outcome": "APPROVED", "decision_authority": "DecisionQualityEngine",
                     "decision_version": "decision-authority-v1", "decision_source": source,
                     "decision_veto_reasons": [], "decision_data_quality": "VALID"})
        return data


def _outcome(*, signal_id="x", net=-2, mfe=0, mae=1, exit_at="2025-01-02T00:00:00+00:00"):
    return ReplayOutcome(signal_id, "v", "BTC", "1h", "LONG", "2025-01-01T01:00:00+00:00",
                         "2025-01-01T01:00:00+00:00", "2025-01-01T01:00:00+00:00", exit_at,
                         100, 100, None, 98, (102,), 98, "STOP", net, 0, 0, 0, None, "NOT_MODELED",
                         net, net / 2, net / 2, mfe, mae, provenance={"evaluation_mode": "HISTORICAL_REPLAY"})


def test_frozen_config_and_dataset_identity_are_deterministic(tmp_path):
    first = FrozenBaselineConfig.capture(symbols=("BTC",), replay_config=ReplayConfig(timeframe="1h"))
    second = FrozenBaselineConfig.capture(symbols=("BTC",), replay_config=ReplayConfig(timeframe="1h"))
    changed = FrozenBaselineConfig.capture(symbols=("BTC",), replay_config=ReplayConfig(timeframe="1h", max_holding_bars=121))
    assert first.config_hash == second.config_hash and first.config_hash != changed.config_hash
    store = DatasetStore(tmp_path)
    one = store.materialize(_frame(), provider="TEST", instrument="BTC", timeframe="1h")
    two = store.materialize(_frame(), provider="TEST", instrument="BTC", timeframe="1h")
    changed_frame = _frame(); changed_frame.loc[10, "close"] += .01
    changed_frame.loc[10, "high"] = max(changed_frame.loc[10, "high"], changed_frame.loc[10, "close"])
    three = store.materialize(changed_frame, provider="TEST", instrument="BTC", timeframe="1h")
    assert one.dataset_id == two.dataset_id and one.dataset_id != three.dataset_id
    invalid = _frame(); invalid.loc[2, "high"] = invalid.loc[2, "low"] - 1
    with pytest.raises(ValueError, match="dataset rejected"):
        store.materialize(invalid, provider="TEST", instrument="BTC", timeframe="1h")


def test_replay_uses_closed_snapshots_and_has_no_runtime_side_effects(tmp_path):
    store = DatasetStore(tmp_path)
    manifest = store.materialize(_frame(), provider="TEST", instrument="BTC", timeframe="1h")
    analyzer = _Analyzer()
    runner = BaselineCensusRunner(FrozenBaselineConfig.capture(symbols=("BTC",)), analyzer=analyzer,
                                  decision_quality=_DecisionQuality())
    result = runner.run(store.load(manifest), manifest)
    assert result["decision_count"] == len(analyzer.snapshots) > 0
    assert all(len(snapshot) < len(_frame()) for snapshot in analyzer.snapshots)
    assert result["approved_count"] == result["decision_count"]
    assert result["outcomes"] and result["limitations"]


def test_calibration_forensics_and_comparison_are_conservative():
    winner = _outcome(signal_id="win", net=2, mfe=.5, mae=.2)
    loss = _outcome(signal_id="loss", net=-2, mfe=.1, mae=1)
    decisions = [{"decision_id": "win", "confidence": 75}, {"decision_id": "loss", "confidence": 75}]
    calibration = confidence_calibration(decisions, [winner, loss])
    assert calibration["70-79"]["trade_count"] == 2 and calibration["70-79"]["win_rate"] == 50
    frame = _frame(40)
    forensic = forensic_label(loss, frame)
    assert forensic["classification"] == "IMMEDIATE_ADVERSE"
    comparison = compare_baselines({"metrics": {"trade_count": 100, "expectancy": 1, "expectancy_r": 1, "profit_factor": 2}},
                                   {"metrics": {"trade_count": 2, "expectancy": .5, "expectancy_r": .5, "profit_factor": 3}})
    assert comparison["verdict"] == "NOT_UNAMBIGUOUSLY_IMPROVED"
    assert "CANDIDATE_INSUFFICIENT_SAMPLE" in comparison["reasons"]

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from services.flow_microstructure import (
    FlowFeatureEngine, FlowIntegrityStatus, FlowShadowGate, aggregate_completed,
    attach_aggressor_flow, validate_flow_frame,
)
from services.flow_alpha_lab import frozen_candidates
from services.flow_relative_value_lab import frozen_relative_value_candidates


def _frame(rows=300):
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=rows, freq="5min", tz="UTC"),
        "open": [100 + i * .01 for i in range(rows)],
        "high": [101 + i * .01 for i in range(rows)],
        "low": [99 + i * .01 for i in range(rows)],
        "close": [100.2 + i * .01 for i in range(rows)],
        "volume": [100.0] * rows, "quote_volume": [10000.0] * rows,
        "trade_count": [50] * rows, "taker_buy_volume": [40.0] * rows,
        "taker_buy_quote_volume": [4000.0] * rows,
    })


def test_exchange_taker_volume_produces_real_delta_without_candle_color():
    result = attach_aggressor_flow(_frame(2))
    assert result.loc[0, "taker_sell_quote_volume"] == 6000
    assert result.loc[0, "delta_quote"] == -2000
    assert result.loc[0, "normalized_delta"] == -.2
    assert list(result["cvd_quote"]) == [-2000, -4000]


def test_flow_integrity_rejects_buy_volume_above_total():
    frame = _frame(2)
    frame.loc[0, "taker_buy_quote_volume"] = 10001
    assert validate_flow_frame(frame).status is FlowIntegrityStatus.CONFLICTING


def test_completed_15m_aggregation_drops_incomplete_group():
    result = aggregate_completed(_frame(7), 3)
    assert len(result) == 2
    assert result.iloc[0]["quote_volume"] == 30000
    assert result.iloc[0]["decision_at"] == pd.Timestamp("2026-01-01T00:15Z")


def test_flow_features_are_causal_under_future_mutation():
    source = _frame()
    before = FlowFeatureEngine().transform(source.iloc[:250].copy())
    changed = source.copy()
    changed.loc[250:, ["close", "taker_buy_quote_volume"]] = [9999, 9999]
    after = FlowFeatureEngine().transform(changed).iloc[:250]
    pd.testing.assert_series_equal(before["delta_zscore"], after["delta_zscore"])
    pd.testing.assert_series_equal(before["price_cvd_divergence"], after["price_cvd_divergence"])


def test_shadow_flow_fails_closed_without_valid_fresh_data():
    assert FlowShadowGate.evaluate("STALE", required_age_seconds=10)["decision"] == "NO_SIGNAL"
    assert FlowShadowGate.evaluate("VALID", required_age_seconds=999)["reason"] == "DATA_INVALID"
    valid = FlowShadowGate.evaluate("VALID", required_age_seconds=5)
    assert valid["decision"] == "OBSERVE_ONLY" and not valid["execution_authority"]


def test_directional_candidate_roster_is_bounded_and_stable():
    candidates = frozen_candidates()
    assert len(candidates) == 12
    assert len({item.candidate_id for item in candidates}) == 12
    assert len({item.family for item in candidates}) == 4


def test_relative_value_candidate_roster_is_preregistered_and_bounded():
    candidates = frozen_relative_value_candidates()
    assert len(candidates) == 9
    assert len({item.candidate_id for item in candidates}) == 9
    assert len({item.family for item in candidates}) == 3
    assert [item.threshold for item in candidates[::3]] == [.75, 1.0, 1.25]


def test_completed_aggregation_preserves_derivatives_integrity_status():
    frame = _frame(3)
    frame["oi_status"] = ["VALID", "GAPPED", "VALID"]
    frame["funding_status"] = "VALID"
    result = aggregate_completed(frame, 3)
    assert result.loc[0, "oi_status"] == "VALID"
    assert result.loc[0, "funding_status"] == "VALID"


def test_final_registry_reconciles_and_protected_splits_remain_sealed():
    root = Path("research_artifacts/flow_alpha")
    reconciliation = json.loads((root / "reconciliation-272b7c00bfbdad04.json").read_text())
    registry = json.loads((root / "experiment-registry.json").read_text())
    assert reconciliation["valid"] and reconciliation["errors"] == []
    assert registry["candidate_count"] == 21 and registry["qualified_count"] == 0
    assert not registry["validation_accessed"] and not registry["blind_accessed"]
    assert not registry["legacy_seen_test_accessed"] and not registry["production_defaults_changed"]


def test_all_flow_experiments_use_three_costs_and_decision_quality():
    registry = json.loads(Path("research_artifacts/flow_alpha/experiment-registry.json").read_text())
    for experiment in registry["experiments"]:
        assert set(experiment["costs"]) == {"BASE", "HIGH", "STRESS"}
        assert experiment["integrity"]["decision_quality"]
        base = experiment["costs"]["BASE"]["expectancy_r"]
        high = experiment["costs"]["HIGH"]["expectancy_r"]
        stress = experiment["costs"]["STRESS"]["expectancy_r"]
        assert base >= high >= stress

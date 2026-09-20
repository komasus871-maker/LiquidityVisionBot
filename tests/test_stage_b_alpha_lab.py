from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from services.decision_quality import DecisionQualityEngine
from services.research_features import ATR, EMA50, EMA200, REGIME
from services.stage_b_alpha_lab import (
    DEVELOPMENT_ACCESS_END, _authorized_plan, frozen_candidates as cycle1_candidates,
    load_development_frames,
)
from services.stage_b_cycle2_lab import (
    CYCLE2_PREREGISTRATION_HASH, _enhanced_features, frozen_candidates as cycle2_candidates,
    preregistration_payload,
)


def _materialization() -> dict:
    paths = list(Path("research_artifacts/stage_b_cycle1").glob("materialization-*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


def test_stage_b_candidate_budget_and_identities_are_deterministic():
    first1, second1 = cycle1_candidates(), cycle1_candidates()
    first2, second2 = cycle2_candidates(), cycle2_candidates()
    assert len(first1) == len({item.config_hash for item in first1}) == 12
    assert len(first2) == len({item.config_hash for item in first2}) == 12
    assert [item.config_hash for item in first1] == [item.config_hash for item in second1]
    assert [item.config_hash for item in first2] == [item.config_hash for item in second2]
    assert CYCLE2_PREREGISTRATION_HASH == "62c729032adbd7b8"
    assert preregistration_payload()["variant_count"] == 12


def test_development_loader_never_reads_validation_rows():
    frames = load_development_frames(_materialization())
    assert set(frames) == {"BTC", "ETH", "SOL", "XRP", "DOGE"}
    for frame in frames.values():
        assert frame.iloc[-1]["time"] == DEVELOPMENT_ACCESS_END
        assert not (frame["time"] > DEVELOPMENT_ACCESS_END).any()


def test_cycle2_features_are_future_insensitive():
    source = load_development_frames(_materialization())["BTC"].iloc[:320].copy()
    short = _enhanced_features(source.iloc[:280].copy())
    long = _enhanced_features(source.copy())
    columns = ["__sb2_expansion", "__sb2_move_6h_atr", "__sb2_prior_high_72", "__sb2_prior_low_72"]
    pd.testing.assert_series_equal(short.iloc[-1][columns], long.iloc[279][columns], check_names=False)


def test_family_plan_requires_trade_integrity_and_decision_quality_authority():
    spec = cycle2_candidates()[6]
    row = {
        "time": pd.Timestamp("2023-01-01T00:00:00Z"), "close": 100.0,
        ATR: {"atr": 2.0}, EMA50: 101.0, EMA200: 95.0,
        REGIME: {"code": "TRENDING", "label": "Trending", "risk_multiplier": 1.0},
        "__sb_regime": "TRENDING",
    }
    quality = DecisionQualityEngine()
    plan, reason = _authorized_plan(spec, "BTC", row, quality)
    assert reason == "APPROVED"
    assert plan is not None
    assert plan["metadata"]["decision_authority"] == DecisionQualityEngine.AUTHORITY
    assert plan["metadata"]["decision_version"] == DecisionQualityEngine.DECISION_VERSION
    assert plan["stop"] < plan["entry"] < plan["targets"][0]


def test_stage_b_modules_are_not_imported_by_production_execution_paths():
    roots = [Path("bot.py"), Path("services/execution.py"), Path("services/unified_core/pipeline.py")]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in roots if path.exists())
    assert "stage_b_alpha_lab" not in combined
    assert "stage_b_cycle2_lab" not in combined


def test_completed_cycle_artifacts_preserve_partition_locks():
    for directory in (Path("research_artifacts/stage_b_cycle1"), Path("research_artifacts/stage_b_cycle2")):
        paths = list(directory.glob("development-*.json"))
        assert len(paths) == 1
        payload = json.loads(paths[0].read_text(encoding="utf-8"))
        assert payload["validation_accessed"] is False
        assert payload["blind_accessed"] is False
        assert payload["protected_existing_partitions_accessed"] == []
        assert len(payload["candidates"]) == 12

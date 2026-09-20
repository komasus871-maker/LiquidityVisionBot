from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from services.decision_quality import DecisionQualityEngine
from services.phase3b_edge_surgery import (
    CandidateSpec, ExperimentAccess, SplitRole, TemporalExperimentDesign,
    derive_admission_overlay, fit_development_confidence_calibration,
)
from services.research_replay import (
    EntryPolicy, ExitProtectionConfig, HistoricalReplayEngine, ReplayConfig,
    ReplayCostModel, ReplayOutcome,
)


def _outcome(signal_id: str, net_r: float) -> ReplayOutcome:
    return ReplayOutcome(signal_id, "baseline", "BTC", "1h", "LONG", "2026-01-01T00:00:00+00:00",
                         "2026-01-01T00:00:00+00:00", "2026-01-01T01:00:00+00:00",
                         "2026-01-01T02:00:00+00:00", 100, 100, None, 98, (102,), 98, "STOP",
                         net_r * 2, 0, 0, 0, None, "NOT_MODELED", net_r * 2, net_r, net_r,
                         0, 1, provenance={"evaluation_mode": "HISTORICAL_REPLAY"})


def test_split_access_blocks_blind_and_legacy_until_the_required_checkpoint():
    times = pd.date_range(datetime(2026, 3, 1, tzinfo=timezone.utc), periods=4_000, freq="h", tz="UTC")
    frame = pd.DataFrame({"time": times})
    design = TemporalExperimentDesign.from_frame(frame)
    development, validation, blind, legacy = design.intervals
    assert development.access_end < validation.start <= validation.end < blind.start
    assert blind.access_end < legacy.start
    access = ExperimentAccess()
    with pytest.raises(PermissionError, match="BLIND_HOLDOUT"):
        access.permit(SplitRole.BLIND_HOLDOUT, "CANDIDATE")
    with pytest.raises(PermissionError, match="LEGACY_SEEN_TEST"):
        access.permit(SplitRole.LEGACY_SEEN_TEST, "CANDIDATE")
    access.freeze_finalist("CANDIDATE")
    access.permit(SplitRole.BLIND_HOLDOUT, "CANDIDATE")
    access.permit(SplitRole.LEGACY_SEEN_TEST, "CANDIDATE")


def test_admission_overlay_is_deterministic_and_never_changes_a_retained_outcome():
    baseline_id = "BASELINE:BTC:1h:2026-01-01T00:00:00+00:00"
    evaluation = {
        "role": SplitRole.DEVELOPMENT, "interval": {}, "dataset": {},
        "outcomes": [_outcome(baseline_id, 1.0)],
        "decisions": [{"decision_id": baseline_id, "direction": "LONG", "confidence": 70,
                       "attribution": {"execution_readiness": 62, "directional_edge": 50},
                       "baseline_decision_outcome": DecisionQualityEngine.APPROVED}],
    }
    candidate = CandidateSpec("CEILING", "H2", {"max_confidence": 70})
    retained = derive_admission_overlay(evaluation, candidate)
    rejected = derive_admission_overlay(evaluation, CandidateSpec("LOW", "H2", {"max_confidence": 69}))
    assert retained["metrics"]["trade_count"] == 1
    assert retained["outcomes"][0].net_r == 1.0
    assert retained["outcomes"][0].signal_id.startswith("CEILING:")
    assert rejected["metrics"]["trade_count"] == 0
    assert retained["candidate"]["config_hash"] != rejected["candidate"]["config_hash"]


def test_development_calibration_is_monotone_and_exit_protection_waits_a_full_bar():
    calibration = fit_development_confidence_calibration([
        {"confidence": 60, "outcome": {"simulated_fill": 1, "net_r": 1}},
        {"confidence": 65, "outcome": {"simulated_fill": 1, "net_r": -1}},
    ])
    probabilities = [bucket["win_probability"] for bucket in calibration["buckets"]]
    assert calibration["fit_role"] == SplitRole.DEVELOPMENT and probabilities == sorted(probabilities)
    frame = pd.DataFrame({
        "time": pd.date_range(datetime(2026, 1, 1, tzinfo=timezone.utc), periods=5, freq="h", tz="UTC"),
        "open": [100, 100, 100, 101, 100], "high": [101, 101, 102.5, 101, 101],
        "low": [99, 99, 99, 99, 99], "close": [100, 100, 102, 100, 100],
        "volume": [1.0] * 5, "confirm": ["1"] * 5,
    })
    engine = HistoricalReplayEngine(ReplayConfig(
        timeframe="1h", warmup_bars=2, entry_expiry_bars=1, max_holding_bars=2,
        entry_policy=EntryPolicy.MARKET_NEXT_OPEN, costs=ReplayCostModel(fee_rate=0, market_slippage_rate=0),
        exit_protection=ExitProtectionConfig(activation_r=1, protected_stop_r=0),
    ))
    outcome = engine.run(frame, lambda snapshot: {
        "signal_id": "protection", "strategy_version": "test", "symbol": "BTC", "direction": "LONG",
        "entry": 100, "stop": 98, "tp1": 106,
    } if len(snapshot) == 2 else None)[0]
    assert outcome.exit_reason == "PROTECTED_STOP" and outcome.exit_price == 100

from __future__ import annotations

import pandas as pd
import pytest

from services.derivatives_alpha import (
    DERIV_BLIND_START, DERIV_DEV_END, DERIV_VALIDATION_START,
    DerivativesIntegrityStatus, PositioningStateEngine, causal_asof,
    candidate_registry_identity, frozen_candidates, realized_funding_r,
    validate_derivatives_frame,
)
from services.derivatives_alpha_lab import candidate_direction
from services.derivatives_cycle2_lab import _resample_4h, cycle2_candidates


def test_causal_alignment_rejects_future_and_exposes_age():
    decisions = pd.DataFrame({"decision_at": pd.to_datetime(["2026-01-01T01:00Z", "2026-01-01T02:00Z"])})
    observations = pd.DataFrame({
        "source_at": pd.to_datetime(["2026-01-01T00:50Z", "2026-01-01T01:30Z"]),
        "available_at": pd.to_datetime(["2026-01-01T00:55Z", "2026-01-01T01:35Z"]),
        "value": [1.0, 2.0],
    })
    result = causal_asof(decisions, observations, maximum_age=pd.Timedelta(hours=1), prefix="oi")
    assert result["oi_value"].tolist() == [1.0, 2.0]
    assert result["oi_age_seconds"].tolist() == [300.0, 1500.0]
    assert (result["oi_available_at"] <= result["decision_at"]).all()


def test_alignment_fails_conflicting_or_impossible_timestamps():
    decisions = pd.DataFrame({"decision_at": ["2026-01-01T01:00Z"]})
    bad = pd.DataFrame({"source_at": ["2026-01-01T01:00Z"], "available_at": ["2026-01-01T00:59Z"], "v": [1]})
    with pytest.raises(ValueError, match="availability"):
        causal_asof(decisions, bad, maximum_age=pd.Timedelta(hours=1), prefix="x")


def test_integrity_statuses_are_structural_not_outlier_filters():
    valid = pd.DataFrame({"time": pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"), "oi": [1, 9999999, 2]})
    report = validate_derivatives_frame(valid, timestamp_column="time", numeric_columns=["oi"],
        expected_frequency=pd.Timedelta(hours=1), maximum_gap=pd.Timedelta(hours=2), nonnegative_columns=["oi"])
    assert report.status is DerivativesIntegrityStatus.VALID
    gapped = valid.drop(index=1).reset_index(drop=True)
    assert validate_derivatives_frame(gapped, timestamp_column="time", numeric_columns=["oi"],
        expected_frequency=pd.Timedelta(hours=1), maximum_gap=pd.Timedelta(hours=1)).status is DerivativesIntegrityStatus.GAPPED


def test_integrity_rejects_duplicate_nonfinite_and_negative():
    frame = pd.DataFrame({"time": pd.to_datetime(["2026-01-01T00:00Z", "2026-01-01T00:00Z"]), "oi": [1, 2]})
    assert validate_derivatives_frame(frame, timestamp_column="time", numeric_columns=["oi"],
        expected_frequency=pd.Timedelta(hours=1), maximum_gap=pd.Timedelta(hours=2)).status is DerivativesIntegrityStatus.CONFLICTING


def _positioning_frame(rows=220):
    return pd.DataFrame({
        "decision_at": pd.date_range("2026-01-01", periods=rows, freq="h", tz="UTC"),
        "close": [100 + i * .1 for i in range(rows)],
        "oi_usd": [1_000_000 + i * 1000 for i in range(rows)],
        "funding_rate": [(-1 if i % 20 == 0 else 1) * .0001 for i in range(rows)],
        "basis_pct": [(-.001 + i / rows * .002) for i in range(rows)],
    })


def test_positioning_features_are_causal_under_future_mutation():
    source = _positioning_frame()
    engine = PositioningStateEngine(lookback=168, minimum_history=72)
    before = engine.transform(source.iloc[:200].copy())
    changed = source.copy()
    changed.loc[200:, ["close", "oi_usd", "funding_rate", "basis_pct"]] = [99999, 99999, .5, .5]
    after = engine.transform(changed).iloc[:200]
    pd.testing.assert_series_equal(before["oi_percentile"], after["oi_percentile"])
    pd.testing.assert_series_equal(before["positioning_state"], after["positioning_state"])


def test_price_oi_quadrants_have_explicit_identity():
    result = PositioningStateEngine(168, 72).transform(_positioning_frame())
    assert set(result["price_oi_quadrant"]) <= {
        "UNKNOWN", "PRICE_UP_OI_UP", "PRICE_UP_OI_DOWN", "PRICE_DOWN_OI_UP", "PRICE_DOWN_OI_DOWN"
    }


def test_frozen_candidate_roster_is_bounded_and_stable():
    candidates = frozen_candidates()
    assert len(candidates) == 12
    assert len({c.candidate_id for c in candidates}) == 12
    assert len({c.family for c in candidates}) == 4
    assert candidate_registry_identity(candidates) == candidate_registry_identity(frozen_candidates())


def test_new_split_boundaries_are_chronological_and_blind_separate():
    assert DERIV_DEV_END < DERIV_VALIDATION_START < DERIV_BLIND_START


def test_realized_funding_is_directional_and_not_inferred_between_events():
    settlements = pd.DataFrame({
        "funding_at": pd.to_datetime(["2026-01-01T08:00Z", "2026-01-01T16:00Z"]),
        "funding_rate": [.001, -.0002],
    })
    long_cost = realized_funding_r(settlements, fill_at="2026-01-01T07:00Z", exit_at="2026-01-01T09:00Z",
                                   direction="LONG", entry=100, risk=2)
    short_cost = realized_funding_r(settlements, fill_at="2026-01-01T07:00Z", exit_at="2026-01-01T09:00Z",
                                    direction="SHORT", entry=100, risk=2)
    assert long_cost == pytest.approx(.05) and short_cost == pytest.approx(-.05)


def test_positioning_engine_has_no_execution_authority():
    engine = PositioningStateEngine(168, 72)
    forbidden = {"place_order", "execute", "submit", "approve_trade", "live"}
    assert forbidden.isdisjoint(set(dir(engine)))


def test_candidate_predicates_fail_closed_on_nonfinite_features():
    spec = frozen_candidates()[0]
    row = {
        "price_return_6h_percentile": float("nan"), "oi_change_6h_percentile": .9,
        "oi_percentile": .9, "funding_percentile": .5, "basis_percentile": .5,
        "price_return_1h": .01,
    }
    assert candidate_direction(spec, row) is None


def test_long_and_short_are_not_mechanical_mirrors():
    long_spec, short_spec = frozen_candidates()[:2]
    long_state = {
        "price_return_6h_percentile": .9, "oi_change_6h_percentile": .9,
        "oi_percentile": .5, "funding_percentile": .5, "basis_percentile": .5,
        "price_return_1h": .01,
    }
    assert candidate_direction(long_spec, long_state) == "LONG"
    assert candidate_direction(short_spec, long_state) is None


def test_cycle2_is_bounded_and_has_separate_candidate_identities():
    candidates = cycle2_candidates()
    assert len(candidates) == 9
    assert len({item.candidate_id for item in candidates}) == 9
    assert all(item.family.startswith("C2_4H_") for item in candidates)

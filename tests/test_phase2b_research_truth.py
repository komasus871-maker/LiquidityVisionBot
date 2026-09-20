from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from services.research_replay import (
    EntryPolicy,
    EvaluationMode,
    HistoricalReplayEngine,
    PerformanceAttribution,
    ReplayConfig,
    ReplayCostModel,
    TemporalSplit,
    walk_forward_windows,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame({
        "time": pd.date_range(START, periods=len(rows), freq="min", tz="UTC"),
        "open": [row[0] for row in rows], "high": [row[1] for row in rows],
        "low": [row[2] for row in rows], "close": [row[3] for row in rows],
        "volume": [1.0] * len(rows), "confirm": ["1"] * len(rows),
    })


def _signal(**updates):
    result = {
        "signal_id": "fixture-1", "strategy_version": "fixture-v1", "symbol": "BTC",
        "direction": "LONG", "entry": 100.0, "stop": 98.0, "tp1": 102.0,
    }
    result.update(updates)
    return result


def _engine(*, policy=EntryPolicy.MARKET_NEXT_OPEN, fee=0.0, slippage=0.0, funding=None, hold=2):
    return HistoricalReplayEngine(ReplayConfig(
        timeframe="1m", warmup_bars=2, entry_expiry_bars=1, max_holding_bars=hold,
        entry_policy=policy, costs=ReplayCostModel(fee_rate=fee, market_slippage_rate=slippage,
                                                    funding_rate_per_bar=funding),
    ))


def _single(engine, frame, signal=None, metadata=None):
    return engine.run(frame, lambda snapshot: (signal or _signal()) if len(snapshot) == 2 else None,
                      metadata=metadata)[0]


def test_closed_candle_clock_and_future_rows_cannot_change_earlier_decision():
    frame = _frame([(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100), (100, 102, 99, 101)])
    seen = []
    def factory(snapshot):
        if len(snapshot) == 2:
            seen.append(snapshot.copy())
            return _signal()
        return None
    outcomes = _engine().run(frame, factory)
    changed = frame.copy()
    changed.loc[3, ["open", "high", "low", "close"]] = [999, 1000, 998, 999]
    seen_after = []
    _engine().run(changed, lambda snapshot: (seen_after.append(snapshot.copy()) or _signal()) if len(snapshot) == 2 else None)
    assert outcomes[0].decision_at == "2026-01-01T00:02:00+00:00"
    assert seen[0]["time"].max() < pd.Timestamp(outcomes[0].decision_at)
    pd.testing.assert_frame_equal(seen[0], seen_after[0])
    assert outcomes[0].provenance["run_id"] and outcomes[0].provenance["dataset_start"]
    assert outcomes[0].provenance["dataset_id"] != _engine().run(
        changed, lambda snapshot: _signal() if len(snapshot) == 2 else None,
    )[0].provenance["dataset_id"]


def test_market_and_limit_entries_are_only_eligible_after_decision():
    frame = _frame([(100, 101, 99, 100), (100, 101, 99, 100), (101, 102, 100.5, 101), (101, 101, 100.5, 101)])
    market = _single(_engine(policy=EntryPolicy.MARKET_NEXT_OPEN, slippage=.01), frame)
    limit = _single(_engine(policy=EntryPolicy.LIMIT_AFTER_DECISION), frame)
    assert market.fill_at >= market.decision_at and market.simulated_fill == pytest.approx(102.01)
    assert market.intended_entry != market.simulated_fill
    assert limit.exit_reason == "ENTRY_EXPIRED"  # pre-decision candle range cannot fill it


@pytest.mark.parametrize(
    ("row", "reason", "price", "flag"),
    [
        ((100, 101, 97, 99), "STOP", 98, None),
        ((100, 103, 99, 102), "TP", 102, None),
        ((100, 103, 97, 100), "STOP", 98, "STOP_TARGET_SAME_CANDLE_CONSERVATIVE_STOP_FIRST"),
        ((96, 97, 95, 96), "STOP", 96, None),
        ((103, 104, 102, 103), "TP", 103, None),
    ],
)
def test_stop_target_and_gap_resolution_is_deterministic(row, reason, price, flag):
    frame = _frame([(100, 101, 99, 100), (100, 101, 99, 100), row, (100, 101, 99, 100)])
    outcome = _single(_engine(), frame)
    assert outcome.exit_reason == reason and outcome.exit_price == pytest.approx(price)
    if flag:
        assert flag in outcome.ambiguity_flags


def test_fees_change_net_not_gross_and_funding_never_claims_exactness_when_missing():
    frame = _frame([(100, 101, 99, 100), (100, 101, 99, 100), (100, 103, 99, 102), (100, 101, 99, 100)])
    free = _single(_engine(fee=0), frame)
    charged = _single(_engine(fee=.001), frame)
    assert charged.gross_pnl == free.gross_pnl
    assert charged.net_pnl < free.net_pnl and charged.total_fee > 0
    assert charged.funding is None and charged.funding_status == "NOT_MODELED"


def test_mfe_mae_exclude_pre_entry_and_post_exit_extremes():
    frame = _frame([
        (100, 101, 99, 100), (100, 101, 99, 100),
        (100, 500, 100, 100),  # limit fills here; its high is not credited
        (100, 101, 99, 100), (100, 101, 99, 100),
    ])
    outcome = _single(_engine(policy=EntryPolicy.LIMIT_AFTER_DECISION, hold=2), frame)
    assert "LIMIT_ENTRY_BAR_PATH_UNOBSERVED" in outcome.ambiguity_flags
    assert outcome.mfe_r == pytest.approx(.5)
    post_exit = _single(_engine(hold=2), _frame([
        (100, 101, 99, 100), (100, 101, 99, 100), (100, 1000, 99, 102), (100, 101, 99, 100),
    ]))
    assert post_exit.exit_reason == "TP" and post_exit.mfe_r == 0.0


def test_historical_frame_uses_phase2a_integrity_rules_and_rejects_gaps():
    frame = _frame([(100, 101, 99, 100)] * 4).drop(index=2).reset_index(drop=True)
    with pytest.raises(ValueError, match="GAPPED"):
        _engine().prepare(frame)


def test_temporal_splits_walk_forward_and_mode_separation_are_reproducible():
    records = [
        {"decision_at": (START + timedelta(minutes=index)).isoformat(), "value": index}
        for index in range(6)
    ]
    split = TemporalSplit(START + timedelta(minutes=2), START + timedelta(minutes=3))
    development, test = split.partition(records)
    windows = walk_forward_windows(START, START + timedelta(minutes=12), train_bars=3, test_bars=2, timeframe="1m")
    assert [row["value"] for row in development] == [0, 1, 2]
    assert [row["value"] for row in test] == [3, 4, 5]
    assert windows and all(window.train_end <= window.test_start for window in windows)
    outcome = _single(_engine(), _frame([(100, 101, 99, 100), (100, 101, 99, 100), (100, 103, 99, 102), (100, 101, 99, 100)]))
    metrics = PerformanceAttribution.metrics([outcome])
    assert metrics["trade_count"] == 1 and metrics["gross_pnl"] == outcome.gross_pnl
    paper_data = outcome.as_dict()
    paper_data.update({"mode": EvaluationMode.PAPER.value,
                       "provenance": {**outcome.provenance, "evaluation_mode": EvaluationMode.PAPER.value}})
    paper = outcome.__class__(**paper_data)
    with pytest.raises(ValueError, match="must not be mixed"):
        PerformanceAttribution.metrics([outcome, paper])
    forged_live = outcome.__class__(**{**outcome.as_dict(), "mode": EvaluationMode.LIVE.value})
    with pytest.raises(ValueError, match="conflicts with outcome provenance"):
        PerformanceAttribution.metrics([forged_live])
    actual_live = outcome.__class__(**{
        **outcome.as_dict(), "mode": EvaluationMode.LIVE.value, "actual_fill": outcome.simulated_fill,
        "provenance": {**outcome.provenance, "evaluation_mode": EvaluationMode.LIVE.value,
                       "execution_evidence": "EXCHANGE_ACTUAL"},
    })
    assert PerformanceAttribution.metrics([actual_live])["evaluation_mode"] == EvaluationMode.LIVE.value


def test_attribution_reconciles_groups_to_outcomes():
    winning = _single(_engine(), _frame([(100, 101, 99, 100), (100, 101, 99, 100), (100, 103, 99, 102), (100, 101, 99, 100)]), metadata={"confidence": 75, "market_regime": "TRENDING", "decision_authority": "fixture-authority", "decision_version": "v1", "sample_role": "OUT_OF_SAMPLE", "split_id": "fold-1", "variants_evaluated": 1})
    losing = _single(_engine(), _frame([(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 97, 98), (100, 101, 99, 100)]), signal=_signal(signal_id="fixture-2"), metadata={"confidence": 45, "market_regime": "RANGING"})
    overall = PerformanceAttribution.metrics([winning, losing])
    grouped = PerformanceAttribution.group([winning, losing], "confidence_bucket")
    assert sum(item["trade_count"] for item in grouped.values()) == overall["trade_count"] == 2
    assert grouped["70-79"]["wins"] == 1 and grouped["40-49"]["losses"] == 1
    assert overall["win_rate"] == 50 and overall["expectancy"] == 0
    assert overall["profit_factor"] == 1 and overall["max_drawdown"] == 2
    assert overall["sample_size"] == 2 and overall["date_range"]["start"] == winning.decision_at
    assert overall["dataset_ids"] and overall["cost_assumptions"][0]["source"] == "MODELED_DEFAULT"
    assert winning.provenance["sample_role"] == "OUT_OF_SAMPLE"
    assert winning.provenance["decision_authority"] == "fixture-authority"
    assert PerformanceAttribution.group([winning], "session")["ASIA_UTC"]["trade_count"] == 1


def test_replay_module_has_no_runtime_or_persistence_authority_imports():
    module = Path("services/research_replay.py")
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imports.update(node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
    forbidden = {"database", "signal_recorder", "copy_execution_engine", "live_execution", "market"}
    assert not any(any(part == denied for part in name.split(".")) for name in imports for denied in forbidden)

    consumers = []
    for root in (Path("handlers"), Path("core"), Path("services")):
        for path in root.rglob("*.py"):
            if path in {module, Path("services/baseline_edge_census.py"), Path("services/phase3b_edge_surgery.py"),
                        Path("services/phase3c_edge_decomposition.py"),
                        Path("services/master_f1_confirmation.py"),
                        Path("services/stage_b_alpha_lab.py"),
                        Path("services/stage_b_cycle2_lab.py"),
                        Path("services/derivatives_alpha_lab.py"),
                        Path("services/derivatives_cycle2_lab.py"),
                        Path("services/flow_alpha_lab.py")}:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if any(isinstance(node, ast.ImportFrom) and node.module == "services.research_replay"
                   for node in ast.walk(tree)) or any(
                       isinstance(node, ast.Import)
                       and any(alias.name == "services.research_replay" for alias in node.names)
                       for node in ast.walk(tree)):
                consumers.append(str(path))
    # The Phase 3A census and Phase 3B/3C research helpers are the only
    # permitted offline consumers. No runtime handler, planner, PAPER, or LIVE
    # component may import replay.
    assert consumers == []

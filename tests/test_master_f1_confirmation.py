from __future__ import annotations

import asyncio
import inspect

import numpy as np
import pandas as pd

import services.master_f1_confirmation as confirmation
from services.decision_quality import DecisionQualityEngine
from services.master_f1_confirmation import F1, FROZEN_F1_CONFIG_HASH, frozen_confirmation_blocks
from services.providers.okx import OKXProvider
from services.research_replay import ReplayCostModel


def test_f1_identity_and_confirmation_boundaries_are_frozen_without_protected_access():
    blocks = frozen_confirmation_blocks()
    assert F1.config_hash == FROZEN_F1_CONFIG_HASH == "c49803034d94792e"
    assert [block.block_id for block in blocks] == ["CONFIRM_A", "CONFIRM_B", "CONFIRM_C"]
    assert blocks[0].start.isoformat() == "2024-09-14T16:00:00+00:00"
    assert blocks[-1].access_end.isoformat() == "2025-09-05T09:00:00+00:00"
    assert all(left.access_end < right.start for left, right in zip(blocks, blocks[1:]))
    source = inspect.getsource(confirmation)
    assert "BLIND_HOLDOUT" not in source
    assert "LEGACY_SEEN_TEST" not in source


def test_okx_historical_cursor_starts_strictly_before_requested_boundary():
    provider = OKXProvider()
    calls = []

    async def resolve(_symbol):
        return {"instId": "BTC-USDT-SWAP", "tickSz": "0.1"}

    async def request(_endpoint, params):
        calls.append(dict(params))
        return {"data": [["1757037600000", "100", "101", "99", "100", "5", "0", "0", "1"]]}

    provider.resolve_instrument = resolve
    provider._request = request
    frame = asyncio.run(provider.get_historical_klines(
        "BTC", interval="1h", limit=50, older_than="2025-09-05T12:00:00Z",
    ))
    assert calls[0]["after"] == "1757073600000"
    assert frame.attrs["historical_older_than"] == "2025-09-05T12:00:00+00:00"
    assert len(frame) == 1


def test_frozen_plan_cost_scenario_uses_canonical_replay_not_posthoc_fee_math():
    times = pd.date_range("2024-01-01T00:00:00Z", periods=230, freq="1h")
    frame = pd.DataFrame({"time": times, "open": np.full(230, 100.0),
                          "high": np.full(230, 101.5), "low": np.full(230, 99.5),
                          "close": np.full(230, 100.5), "volume": np.full(230, 10.0),
                          "confirm": ["1"] * 230})
    decision_at = (times[219] + pd.Timedelta(hours=1)).isoformat()
    signal_id = f"{F1.candidate_id}:BTC:1h:{decision_at}"
    evaluation = {
        "blocks": [{"id": "CONFIRM_A", "start": decision_at, "end": decision_at,
                    "access_end": (times[-1] + pd.Timedelta(hours=1)).isoformat()}],
        "dataset": {"timeframe": "1h", "provider": "FIXTURE"},
        "decisions": [{"f1_decision_id": signal_id, "decision_at": decision_at,
                       "f1_decision_outcome": DecisionQualityEngine.APPROVED,
                       "symbol": "BTC", "direction": "LONG", "entry": 100.0,
                       "stop": 99.0, "tp1": 101.0, "entry_type": "PLANNED_ZONE"}],
    }
    base = confirmation.replay_frozen_f1_cost_scenario(
        evaluation, frame, costs=ReplayCostModel(fee_rate=.0005, market_slippage_rate=.0003),
    )[0]
    stress = confirmation.replay_frozen_f1_cost_scenario(
        evaluation, frame, costs=ReplayCostModel(fee_rate=.001, market_slippage_rate=.001),
    )[0]
    assert base.signal_id == stress.signal_id == signal_id
    assert base.simulated_fill == stress.simulated_fill == 100.0
    assert base.exit_reason == stress.exit_reason == "TP"
    assert stress.net_r < base.net_r

"""Materialize the one pre-registered, older public-OKX F1 dataset.

This command is deliberately materialization-only: it cannot run the Analyzer,
evaluate F1, read any protected Phase 3B split, or change a runtime setting.
It refuses to refetch after all three immutable manifests exist.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from services.baseline_edge_census import DatasetStore
from services.providers.okx import OKXProvider


ROOT = Path("research_artifacts/master_f1_confirmation")
SYMBOLS = ("BTC", "ETH", "SOL")
TIMEFRAME = "1h"
START = pd.Timestamp("2024-09-05T12:00:00Z")
EXISTING_START = pd.Timestamp("2025-09-05T12:00:00Z")
EXPECTED_CANDLES = 8760


async def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    existing = sorted(ROOT.glob("*.manifest.json"))
    if len(existing) == len(SYMBOLS):
        print("confirmation dataset already materialized; refusing refetch")
        for item in existing:
            print(item.read_text(encoding="utf-8"))
        return
    if existing:
        raise RuntimeError("partial confirmation materialization exists; inspect it before any retry")

    store = DatasetStore(ROOT)
    provider = OKXProvider()
    manifests = []
    for symbol in SYMBOLS:
        raw = await provider.get_historical_klines(
            symbol, interval=TIMEFRAME, limit=9000, older_than=EXISTING_START,
        )
        timestamps = pd.to_datetime(raw["time"], utc=True, errors="raise")
        frame = raw.loc[(timestamps >= START) & (timestamps < EXISTING_START)].copy()
        frame.attrs.update(raw.attrs)
        if len(frame) != EXPECTED_CANDLES:
            raise RuntimeError(f"{symbol} expected {EXPECTED_CANDLES} new candles, received {len(frame)}")
        manifests.append(store.materialize(
            frame, provider="OKX_PUBLIC_SWAP", instrument=symbol, timeframe=TIMEFRAME,
            retrieved_at=datetime.now(timezone.utc),
        ).as_dict())

    request = {
        "schema": "master-f1-confirmation-materialization-v1",
        "symbols": list(SYMBOLS), "timeframe": TIMEFRAME,
        "start": START.isoformat(), "end_exclusive": EXISTING_START.isoformat(),
        "expected_candles_per_symbol": EXPECTED_CANDLES,
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "manifests": manifests,
    }
    request["artifact_hash"] = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    (ROOT / f"materialization-{request['artifact_hash']}.json").write_text(
        json.dumps(request, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())

"""Materialize the preregistered Stage B Cycle 1 public OKX universe."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from services.baseline_edge_census import DatasetStore
from services.providers.okx import OKXProvider


ROOT = Path("research_artifacts/stage_b_cycle1")
SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
TIMEFRAME = "1h"
START = pd.Timestamp("2022-09-01T00:00:00Z")
END = pd.Timestamp("2024-09-05T11:00:00Z")
SPLITS = {
    "DEVELOPMENT": {"start": "2022-09-10T04:00:00+00:00", "end": "2023-09-01T23:00:00+00:00",
                    "access_end": "2023-09-07T23:00:00+00:00"},
    "VALIDATION": {"start": "2023-09-08T00:00:00+00:00", "end": "2024-03-01T23:00:00+00:00",
                   "access_end": "2024-03-07T23:00:00+00:00"},
    "BLIND_HOLDOUT": {"start": "2024-03-08T00:00:00+00:00", "end": "2024-08-30T23:00:00+00:00",
                      "access_end": "2024-09-05T00:00:00+00:00"},
}
PREREGISTRATION = {
    "schema": "stage-b-cycle1-preregistration-v1",
    "symbols": SYMBOLS,
    "timeframe": TIMEFRAME,
    "requested_start": START.isoformat(),
    "requested_end": END.isoformat(),
    "splits": SPLITS,
    "selection": "FIXED_ESTABLISHED_LIQUID_OKX_USDT_SWAPS_BEFORE_OUTCOMES",
    "max_family_variants": 12,
}
PREREGISTRATION_HASH = hashlib.sha256(
    json.dumps(PREREGISTRATION, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()[:16]


async def _fetch_symbol(symbol: str) -> pd.DataFrame:
    provider = OKXProvider()
    chunks: list[pd.DataFrame] = []
    cursor = END + pd.Timedelta(hours=1)
    for _ in range(3):
        frame = await provider.get_historical_klines(
            symbol, interval=TIMEFRAME, limit=12000, older_than=cursor,
        )
        chunks.append(frame)
        oldest = pd.to_datetime(frame["time"], utc=True).min()
        if oldest <= START:
            break
        cursor = oldest
    combined = pd.concat(chunks, ignore_index=True)
    combined["time"] = pd.to_datetime(combined["time"], utc=True)
    combined = combined.drop_duplicates(subset=["time"], keep="first").sort_values("time").reset_index(drop=True)
    combined = combined.loc[(combined["time"] >= START) & (combined["time"] <= END)].copy()
    expected = pd.date_range(START, END, freq="h")
    actual = pd.DatetimeIndex(combined["time"])
    if not actual.equals(expected):
        missing = expected.difference(actual)
        extra = actual.difference(expected)
        raise RuntimeError(
            f"{symbol} does not satisfy frozen window: rows={len(actual)} expected={len(expected)} "
            f"missing={len(missing)} extra={len(extra)}"
        )
    combined.attrs.update({
        "exchange": "OKX", "market_data_provider": "OKX_PUBLIC_SWAP",
        "market_data_symbol": symbol, "market_data_timeframe": TIMEFRAME,
        "market_data_closed_semantics": "CLOSED_ONLY",
    })
    return combined


async def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    prereg_path = ROOT / f"preregistration-{PREREGISTRATION_HASH}.json"
    if not prereg_path.exists():
        prereg_path.write_text(json.dumps(PREREGISTRATION, indent=2, sort_keys=True), encoding="utf-8")
    store = DatasetStore(ROOT)
    manifests = {}
    for symbol in SYMBOLS:
        frame = await _fetch_symbol(symbol)
        manifest = store.materialize(
            frame, provider="OKX_PUBLIC_SWAP", instrument=symbol, timeframe=TIMEFRAME,
            retrieved_at=datetime.now(timezone.utc),
        )
        manifests[symbol] = manifest.as_dict()
        print(json.dumps({"symbol": symbol, "dataset_id": manifest.dataset_id,
                          "candles": manifest.candle_count, "content_hash": manifest.content_hash}))
    payload = {
        "schema": "stage-b-cycle1-materialization-v1",
        "preregistration_hash": PREREGISTRATION_HASH,
        "preregistration": PREREGISTRATION,
        "datasets": manifests,
        "outcome_fields_accessed": False,
        "protected_existing_partitions_accessed": [],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    target = ROOT / f"materialization-{digest}.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"artifact": str(target), "preregistration_hash": PREREGISTRATION_HASH}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

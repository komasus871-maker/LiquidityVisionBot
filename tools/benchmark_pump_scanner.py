"""Offline CPU/memory benchmark for the broad scanner's pure analysis path."""
from __future__ import annotations

import json
import time
import tracemalloc
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from services.pump_dump_scanner import (
    Candle, PumpDumpScanner, ScannerSettings, build_symbol_snapshot, resource_budget,
)


def main() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    for index in range(241):
        price = 100 + index * .01
        if index >= 236:
            price *= 1 + .06 * ((index - 235) / 5)
        candles.append(Candle(start + timedelta(minutes=index), price, price * 1.001,
                              price * .999, price, 1000 if index < 240 else 4000, 100))
    tracemalloc.start()
    began = time.perf_counter()
    base = build_symbol_snapshot(symbol="S00USDT", venue="BINANCE", candles=candles,
                                 quote_volume_24h=100_000_000,
                                 observed_at=start + timedelta(minutes=241))
    snapshots = [replace(base, symbol=f"S{index:02d}USDT") for index in range(40)]
    detector, settings = PumpDumpScanner(), ScannerSettings()
    event_count = 0
    cycles = 100
    for _ in range(cycles):
        for snapshot in snapshots:
            event_count += len(detector.detect(snapshot, settings))
    elapsed = time.perf_counter() - began
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({
        "symbols": len(snapshots), "cycles": cycles, "evaluations": len(snapshots) * cycles,
        "events": event_count, "elapsed_ms": round(elapsed * 1000, 2),
        "milliseconds_per_symbol_evaluation": round(elapsed * 1000 / (len(snapshots) * cycles), 6),
        "peak_python_bytes": peak, "request_budget": resource_budget(),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

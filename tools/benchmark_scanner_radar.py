"""Deterministic, network-free broad-radar capacity benchmark.

This measures only the local candle -> baseline -> anomaly stage. Provider
latency and rate-limit acceptance still require deployed observation, so the
tool never changes the configured production universe.
"""
from __future__ import annotations

import argparse
import ctypes
import gc
import json
import os
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from typing import Any

from services.pump_dump_scanner import (
    Candle, PumpDumpScanner, ScannerSettings, build_symbol_snapshot,
)


SIZES = (40, 75, 100, 150, 200)


def _rss_mb() -> float | None:
    try:
        if os.name == "nt":
            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = (
                ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong,
            )
            process = ctypes.windll.kernel32.GetCurrentProcess()
            if not ctypes.windll.psapi.GetProcessMemoryInfo(
                process, ctypes.byref(counters), counters.cb,
            ):
                return None
            return counters.WorkingSetSize / 1_048_576
        with open("/proc/self/statm", encoding="ascii") as handle:
            statm = handle.read().split()
        return int(statm[1]) * int(os.sysconf("SC_PAGE_SIZE")) / 1_048_576
    except (AttributeError, IndexError, OSError, ValueError):
        return None


def _candles(seed: int) -> list[Candle]:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    price = 100 + seed / 10
    rows = []
    for index in range(241):
        drift = ((index + seed) % 11 - 5) * 0.00004
        close = price * (1 + drift)
        rows.append(Candle(
            opened_at=started + timedelta(minutes=index), open=price,
            high=max(price, close) * 1.0004, low=min(price, close) * .9996,
            close=close, quote_volume=1_000_000 + ((index + seed) % 17) * 20_000,
            trade_count=1_000 + index,
        ))
        price = close
    return rows


def benchmark(sizes: tuple[int, ...] = SIZES) -> dict[str, Any]:
    detector = PumpDumpScanner()
    settings = ScannerSettings()
    result = []
    for size in sizes:
        gc.collect()
        rss_before = _rss_mb()
        tracemalloc.start()
        started = time.perf_counter()
        cpu_started = time.process_time()
        snapshots = [
            build_symbol_snapshot(
                symbol=f"S{index:03}USDT", venue="BINANCE", candles=_candles(index),
                change_24h_pct=0.0, quote_volume_24h=100_000_000,
            ) for index in range(size)
        ]
        shortlisted = sum(bool(detector.detect(snapshot, settings)) for snapshot in snapshots)
        cpu_seconds = time.process_time() - cpu_started
        wall_seconds = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after = _rss_mb()
        result.append({
            "universe": size,
            "wall_seconds": round(wall_seconds, 4),
            "cpu_seconds": round(cpu_seconds, 4),
            "python_peak_mb": round(peak / 1_048_576, 3),
            "process_rss_before_mb": round(rss_before, 3) if rss_before is not None else None,
            "process_rss_after_mb": round(rss_after, 3) if rss_after is not None else None,
            "process_rss_delta_mb": (
                round(rss_after - rss_before, 3)
                if rss_before is not None and rss_after is not None else None
            ),
            "shortlisted": shortlisted,
            "http_requests_per_cycle": size + 1,
            "projected_requests_per_minute_at_60s": size + 1,
            "estimated_request_weight_per_cycle": 40 + 2 * size,
            "estimated_documented_weight_utilization_pct": round(
                (40 + 2 * size) / 2400 * 100, 2,
            ),
            "modeled_postgres_mutations_without_events": 5,
        })
    return {
        "classification": "LOCAL_BROAD_STAGE_CAPACITY_ONLY",
        "network_measured": False,
        "provider_rate_limit_certified": False,
        "postgres_mutation_model": (
            "lease upsert + runtime start + inactive-episode update + runtime finish + lease release; "
            "outcome/event mutations are additional and reported by runtime counters"
        ),
        "configured_universe_unchanged": True,
        "results": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = benchmark()
    print(json.dumps(report, indent=2 if args.json else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

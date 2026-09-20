"""Benchmark and reconcile the exact accelerated MASTER F1 replay."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from services.baseline_edge_census import DatasetManifest, DatasetStore
from services.master_f1_confirmation import F1, F1ConfirmationRunner, frozen_confirmation_blocks


ROOT = Path("research_artifacts/master_f1_confirmation")
DECISION_FIELDS = (
    "decision_at", "baseline_decision_outcome", "f1_decision_outcome", "direction",
    "confidence", "quality_score", "entry", "stop", "tp1", "rr", "entry_type",
    "decision_veto_reasons", "attribution", "phase3c_family",
)


def _manifest(symbol: str) -> DatasetManifest:
    for path in ROOT.glob("*.manifest.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("instrument") == symbol:
            return DatasetManifest(**value)
    raise RuntimeError(f"missing manifest for {symbol}")


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _peak_working_set_mb() -> float | None:
    if os.name != "nt":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(ProcessMemoryCounters), ctypes.c_ulong,
    )
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    handle = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return None
    return round(counters.PeakWorkingSetSize / (1024 * 1024), 3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", choices=("BTC", "ETH", "SOL"), default="BTC")
    args = parser.parse_args()
    symbol = args.symbol
    manifest = _manifest(symbol)
    frame = DatasetStore(ROOT).load(manifest)
    anchor = None
    if symbol != "BTC":
        btc_manifest = _manifest("BTC")
        anchor = DatasetStore(ROOT).load(btc_manifest)
    started = time.perf_counter()
    evaluation = F1ConfirmationRunner().evaluate(
        frame, manifest, blocks=frozen_confirmation_blocks(), anchor_frame=anchor,
    )
    elapsed = time.perf_counter() - started
    decisions = [{key: row.get(key) for key in DECISION_FIELDS} for row in evaluation["decisions"]]
    baseline = [item.as_dict() for item in evaluation["baseline_outcomes"]]
    f1 = [item.as_dict() for item in evaluation["f1_outcomes"]]
    checkpoint = ROOT / f"base-evaluation-{symbol}-{manifest.dataset_id}-{F1.config_hash}.json"
    expected = json.loads(checkpoint.read_text(encoding="utf-8"))["evaluation"] if checkpoint.exists() else None
    expected_decisions = ([{key: row.get(key) for key in DECISION_FIELDS}
                           for row in expected["decisions"]] if expected else None)
    report = {
        "symbol": symbol,
        "dataset_id": manifest.dataset_id,
        "elapsed_seconds": round(elapsed, 6),
        "peak_working_set_mb": _peak_working_set_mb(),
        "decision_count": len(decisions),
        "baseline_outcome_count": len(baseline),
        "f1_outcome_count": len(f1),
        "hashes": {
            "decisions": _digest(decisions),
            "baseline_outcomes": _digest(baseline),
            "f1_outcomes": _digest(f1),
        },
        "checkpoint_parity": ({
            "decisions": _digest(decisions) == _digest(expected_decisions),
            "baseline_outcomes": _digest(baseline) == _digest(expected["baseline_outcomes"]),
            "f1_outcomes": _digest(f1) == _digest(expected["f1_outcomes"]),
        } if expected else None),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

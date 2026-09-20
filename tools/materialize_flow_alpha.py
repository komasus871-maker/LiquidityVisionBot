"""Materialize genuine 5m taker-flow/OI/funding data; never evaluates outcomes."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from services.flow_microstructure import FLOW_SCHEMA, attach_aggressor_flow, validate_flow_frame


ROOT = Path("research_artifacts/flow_alpha")
BASE = "https://data.binance.vision/data/futures/um"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
RANGES = {
    "FLOW_DEV": (pd.Timestamp("2023-01-01T00:00:00Z"), pd.Timestamp("2025-09-30T23:55:00Z")),
    "FLOW_VALIDATION_SEALED": (pd.Timestamp("2025-10-01T00:00:00Z"), pd.Timestamp("2026-01-31T23:55:00Z")),
    "FLOW_BLIND_SEALED": (pd.Timestamp("2026-07-01T00:00:00Z"), pd.Timestamp("2026-08-31T23:55:00Z")),
}
HEADERS = {"User-Agent": "LiquidityVision-flow-research/1.0", "Accept": "application/zip"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch(url: str, attempts: int = 5) -> bytes:
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError):
            if attempt + 1 == attempts:
                raise
            time.sleep(.25 * (2**attempt))
    raise AssertionError("unreachable")


def _csv(payload: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError("source archive must contain exactly one CSV")
        raw = archive.read(names[0])
        first = raw.splitlines()[0].decode("utf-8-sig").split(",", 1)[0]
        if first in {"open_time", "create_time", "calc_time"}:
            return pd.read_csv(io.BytesIO(raw))
        names = [
            "open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
        ]
        return pd.read_csv(io.BytesIO(raw), header=None, names=names)


def _download(urls: list[str], workers: int) -> tuple[pd.DataFrame, list[dict]]:
    frames, receipts = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            payload = future.result()
            frames.append(_csv(payload))
            receipts.append({"url": url, "bytes": len(payload), "sha256": _sha(payload)})
    return pd.concat(frames, ignore_index=True), sorted(receipts, key=lambda item: item["url"])


def _months(start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    cursor, finish = start.tz_localize(None).to_period("M"), end.tz_localize(None).to_period("M")
    result = []
    while cursor <= finish:
        result.append(str(cursor))
        cursor += 1
    return result


def _days(start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    cursor, finish, result = start.date(), end.date(), []
    while cursor <= finish:
        result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def _selected_months() -> list[str]:
    values = set()
    for start, end in RANGES.values():
        values.update(_months(start, end))
    return sorted(values)


def _selected_days() -> list[str]:
    values = set()
    for start, end in RANGES.values():
        values.update(_days(start, end))
    return sorted(values)


def _timestamp(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    micros = numeric.abs() >= 1e14
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")
    result.loc[~micros] = pd.to_datetime(numeric.loc[~micros], unit="ms", utc=True, errors="coerce")
    result.loc[micros] = pd.to_datetime(numeric.loc[micros], unit="us", utc=True, errors="coerce")
    return result


def materialize_symbol(symbol: str, workers: int) -> tuple[pd.DataFrame, dict]:
    months, days = _selected_months(), _selected_days()
    kline_urls = [f"{BASE}/monthly/klines/{symbol}/5m/{symbol}-5m-{month}.zip" for month in months]
    metric_urls = [f"{BASE}/daily/metrics/{symbol}/{symbol}-metrics-{day}.zip" for day in days]
    funding_urls = [f"{BASE}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip" for month in months]
    klines, kline_receipts = _download(kline_urls, workers)
    metrics, metric_receipts = _download(metric_urls, workers)
    funding, funding_receipts = _download(funding_urls, workers)

    flow = klines.rename(columns={"count": "trade_count"}).copy()
    flow["time"] = _timestamp(flow["open_time"])
    flow["close_at"] = _timestamp(flow["close_time"])
    keep = [
        "time", "close_at", "open", "high", "low", "close", "volume", "quote_volume",
        "trade_count", "taker_buy_volume", "taker_buy_quote_volume",
    ]
    flow = flow[keep].sort_values("time").drop_duplicates("time", keep=False).reset_index(drop=True)
    selected = pd.Series(False, index=flow.index)
    for start, end in RANGES.values():
        selected |= flow["time"].between(start, end)
    flow = flow.loc[selected].reset_index(drop=True)
    flow["decision_at"] = flow["time"] + pd.Timedelta(minutes=5)

    metrics = metrics.rename(columns={
        "sum_open_interest": "oi_base", "sum_open_interest_value": "oi_usd",
        "count_toptrader_long_short_ratio": "toptrader_account_ratio",
        "sum_toptrader_long_short_ratio": "toptrader_position_ratio",
        "count_long_short_ratio": "account_long_short_ratio",
        "sum_taker_long_short_vol_ratio": "source_taker_long_short_ratio",
    })
    metrics["oi_source_at"] = pd.to_datetime(metrics["create_time"], utc=True)
    metrics["oi_available_at"] = metrics["oi_source_at"]
    metrics = metrics.sort_values("oi_available_at")
    oi_columns = [
        "oi_source_at", "oi_available_at", "oi_base", "oi_usd", "toptrader_account_ratio",
        "toptrader_position_ratio", "account_long_short_ratio", "source_taker_long_short_ratio",
    ]
    flow = pd.merge_asof(
        flow.sort_values("decision_at"), metrics[oi_columns], left_on="decision_at", right_on="oi_available_at",
        direction="backward", tolerance=pd.Timedelta(minutes=6),
    )
    flow["oi_age_seconds"] = (flow["decision_at"] - flow["oi_available_at"]).dt.total_seconds()

    funding["funding_at"] = _timestamp(funding["calc_time"])
    funding["funding_available_at"] = funding["funding_at"]
    funding = funding.rename(columns={"last_funding_rate": "funding_rate"}).sort_values("funding_available_at")
    flow = pd.merge_asof(
        flow.sort_values("decision_at"),
        funding[["funding_at", "funding_available_at", "funding_rate", "funding_interval_hours"]],
        left_on="decision_at", right_on="funding_available_at", direction="backward",
        tolerance=pd.Timedelta(hours=12),
    )
    flow["funding_age_seconds"] = (flow["decision_at"] - flow["funding_available_at"]).dt.total_seconds()
    flow["oi_status"] = flow["oi_usd"].notna().map({True: "VALID", False: "GAPPED"})
    flow["funding_status"] = flow["funding_rate"].notna().map({True: "VALID", False: "GAPPED"})
    flow["provider"] = "BINANCE_UM"
    flow["provider_symbol"] = symbol
    flow["flow_semantics"] = "EXCHANGE_REPORTED_TAKER_BUY_AGGREGATE"
    flow = attach_aggressor_flow(flow)

    component_receipts = {
        "flow_klines": kline_receipts, "oi_metrics": metric_receipts, "funding": funding_receipts,
    }
    all_receipts = [item for values in component_receipts.values() for item in values]
    return flow, {
        "symbol": symbol, "provider": "BINANCE_UM", "source_files": len(all_receipts),
        "source_bytes": sum(item["bytes"] for item in all_receipts),
        "source_hash": _sha("".join(item["sha256"] for item in sorted(all_receipts, key=lambda x: x["url"])).encode()),
        "component_hashes": {
            name: _sha("".join(item["sha256"] for item in values).encode())
            for name, values in component_receipts.items()
        },
        "oi_gapped_rows": int((flow["oi_status"] != "VALID").sum()),
        "funding_gapped_rows": int((flow["funding_status"] != "VALID").sum()),
    }


def _write(frame: pd.DataFrame, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    serial = frame.copy()
    for column in serial:
        if pd.api.types.is_datetime64_any_dtype(serial[column]):
            serial[column] = serial[column].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    serial.to_csv(
        path, index=False, lineterminator="\n", float_format="%.12g",
        compression={"method": "gzip", "compresslevel": 6, "mtime": 0},
    )
    payload = path.read_bytes()
    return {"path": path.as_posix(), "rows": len(frame), "bytes": len(payload), "sha256": _sha(payload)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.workers <= 32:
        raise ValueError("workers must be 1..32")
    manifest = {
        "schema": FLOW_SCHEMA, "provider": "BINANCE_UM", "symbols": list(SYMBOLS),
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "outcomes_evaluated": False,
        "validation_accessed": False, "blind_accessed": False, "protected_gap_accessed": False,
        "sources": {}, "splits": {},
    }
    expected_rows = {"FLOW_DEV": 289152, "FLOW_VALIDATION_SEALED": 35424, "FLOW_BLIND_SEALED": 17856}
    for symbol in SYMBOLS:
        frame, source = materialize_symbol(symbol, args.workers)
        manifest["sources"][symbol] = source
        for role, (start, end) in RANGES.items():
            split = frame[frame["time"].between(start, end)].copy()
            if len(split) != expected_rows[role]:
                raise ValueError(f"{symbol}/{role} rows {len(split)} != {expected_rows[role]}")
            report = validate_flow_frame(split)
            if not report.valid:
                raise ValueError(f"{symbol}/{role} integrity {report.status}/{report.code}")
            manifest["splits"].setdefault(role, {})[symbol] = _write(
                split, ROOT / "datasets" / role / f"{symbol}_5m.csv.gz",
            ) | {"integrity": {"status": report.status.value, "code": report.code}}
    identity = _sha(json.dumps(manifest, sort_keys=True, default=str).encode())[:16]
    path = ROOT / f"materialization-{identity}.json"
    path.write_text(json.dumps(manifest | {"materialization_id": identity}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

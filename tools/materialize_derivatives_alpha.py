"""Materialize the frozen public derivatives dataset; never evaluates outcomes."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from services.derivatives_alpha import (
    DERIV_BLIND_END, DERIV_BLIND_START, DERIV_DEV_END, DERIV_DEV_START,
    DERIV_VALIDATION_END, DERIV_VALIDATION_START, SCHEMA_VERSION,
    validate_derivatives_frame,
)


ROOT = Path("research_artifacts/derivatives_alpha")
BASE = "https://data.binance.vision/data"
START = pd.Timestamp("2024-07-01T00:00:00Z")
END = pd.Timestamp("2026-06-30T23:00:00Z")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
UA = {"User-Agent": "LiquidityVision-research-public-data/1.0", "Accept": "application/zip"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch(url: str, attempts: int = 5) -> bytes:
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError):
            if attempt + 1 == attempts:
                raise
            time.sleep(.25 * 2**attempt)
    raise AssertionError("unreachable")


def _csv_from_zip(payload: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError("archive must contain exactly one CSV")
        raw = archive.read(names[0])
        first = raw.splitlines()[0].decode("utf-8-sig").split(",", 1)[0].strip()
        if first in {"open_time", "calc_time", "create_time"}:
            return pd.read_csv(io.BytesIO(raw))
        # Spot and price-index archives are not consistent about including a
        # header.  Detect the content, never consume the first observation as
        # a header, and apply the published 12-column kline schema.
        columns = [
            "open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
        ]
        return pd.read_csv(io.BytesIO(raw), header=None, names=columns)


def _months(start: pd.Timestamp, end: pd.Timestamp) -> Iterable[str]:
    cursor = start.tz_localize(None).to_period("M")
    finish = end.tz_localize(None).to_period("M")
    while cursor <= finish:
        yield str(cursor)
        cursor += 1


def _days(start: pd.Timestamp, end: pd.Timestamp) -> Iterable[str]:
    cursor, finish = start.date(), end.date()
    while cursor <= finish:
        yield cursor.isoformat()
        cursor += timedelta(days=1)


def _time(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().all():
        # Binance migrated some archives from millisecond to microsecond Unix
        # timestamps.  A 24-month concatenation can contain both encodings, so
        # infer per row rather than from the aggregate median.
        micros = numeric.abs() >= 1e14
        result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")
        result.loc[~micros] = pd.to_datetime(numeric.loc[~micros], unit="ms", utc=True, errors="coerce")
        result.loc[micros] = pd.to_datetime(numeric.loc[micros], unit="us", utc=True, errors="coerce")
        return result
    return pd.to_datetime(values, utc=True, errors="coerce")


def _download_many(urls: list[str], workers: int) -> tuple[list[pd.DataFrame], list[dict]]:
    frames: list[pd.DataFrame] = []
    receipts: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            payload = future.result()
            frames.append(_csv_from_zip(payload))
            receipts.append({"url": url, "bytes": len(payload), "sha256": _sha(payload)})
    return frames, sorted(receipts, key=lambda item: item["url"])


def _monthly(symbol: str, kind: str, workers: int, *, spot: bool = False) -> tuple[pd.DataFrame, list[dict]]:
    market = "spot" if spot else "futures/um"
    urls = []
    for month in _months(START, END):
        if kind == "fundingRate":
            path = f"{market}/monthly/{kind}/{symbol}/{symbol}-{kind}-{month}.zip"
        else:
            path = f"{market}/monthly/{kind}/{symbol}/1h/{symbol}-1h-{month}.zip"
        urls.append(f"{BASE}/{path}")
    frames, receipts = _download_many(urls, workers)
    return pd.concat(frames, ignore_index=True), receipts


def _metrics(symbol: str, workers: int) -> tuple[pd.DataFrame, list[dict]]:
    urls = [
        f"{BASE}/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day}.zip"
        for day in _days(START, END)
    ]
    frames, receipts = _download_many(urls, workers)
    return pd.concat(frames, ignore_index=True), receipts


def _supplement_price_gaps(
    symbol: str, kind: str, raw: pd.DataFrame, receipts: list[dict], workers: int,
    *, spot: bool = False,
) -> tuple[pd.DataFrame, list[dict]]:
    probe = _price(raw, "probe")
    expected = pd.date_range(START, END, freq="h")
    missing = expected.difference(pd.DatetimeIndex(probe["time"]))
    if missing.empty:
        return raw, receipts
    market = "spot" if spot else "futures/um"
    days = sorted({stamp.date().isoformat() for stamp in missing})
    urls = [
        f"{BASE}/{market}/daily/{kind}/{symbol}/1h/{symbol}-1h-{day}.zip"
        for day in days
    ]
    frames, additions = _download_many(urls, workers)
    combined = pd.concat([raw, *frames], ignore_index=True).drop_duplicates()
    return combined, sorted([*receipts, *additions], key=lambda item: item["url"])


def _price(frame: pd.DataFrame, prefix: str, *, full_ohlcv: bool = False) -> pd.DataFrame:
    frame = frame.copy()
    frame["time"] = _time(frame.iloc[:, 0])
    columns = list(frame.columns)
    names = ["open", "high", "low", "close"]
    keep = {"time": "time"}
    for offset, name in enumerate(names, start=1):
        keep[columns[offset]] = name if full_ohlcv else f"{prefix}_{name}"
    if full_ohlcv:
        keep[columns[5]] = "volume"
    result = frame[list(keep)].rename(columns=keep)
    result = result[(result["time"] >= START) & (result["time"] <= END)]
    return result.sort_values("time").drop_duplicates("time", keep=False).reset_index(drop=True)


def materialize_symbol(symbol: str, workers: int) -> tuple[pd.DataFrame, dict]:
    receipts: dict[str, list[dict]] = {}
    perp_raw, receipts["perpetual"] = _monthly(symbol, "klines", workers)
    mark_raw, receipts["mark"] = _monthly(symbol, "markPriceKlines", workers)
    index_raw, receipts["index"] = _monthly(symbol, "indexPriceKlines", workers)
    premium_raw, receipts["premium"] = _monthly(symbol, "premiumIndexKlines", workers)
    spot_raw, receipts["spot"] = _monthly(symbol, "klines", workers, spot=True)
    funding_raw, receipts["funding"] = _monthly(symbol, "fundingRate", workers)
    metrics_raw, receipts["metrics"] = _metrics(symbol, workers)

    mark_raw, receipts["mark"] = _supplement_price_gaps(
        symbol, "markPriceKlines", mark_raw, receipts["mark"], workers,
    )
    index_raw, receipts["index"] = _supplement_price_gaps(
        symbol, "indexPriceKlines", index_raw, receipts["index"], workers,
    )
    premium_raw, receipts["premium"] = _supplement_price_gaps(
        symbol, "premiumIndexKlines", premium_raw, receipts["premium"], workers,
    )
    spot_raw, receipts["spot"] = _supplement_price_gaps(
        symbol, "klines", spot_raw, receipts["spot"], workers, spot=True,
    )

    result = _price(perp_raw, "perp", full_ohlcv=True)
    for prefix, raw in (("mark", mark_raw), ("index", index_raw), ("premium", premium_raw), ("spot", spot_raw)):
        component = _price(raw, prefix)
        result = result.merge(component, on="time", how="inner", validate="one_to_one")
    expected = pd.date_range(START, END, freq="h")
    if not pd.DatetimeIndex(result["time"]).equals(expected):
        missing = expected.difference(pd.DatetimeIndex(result["time"]))
        raise ValueError(f"{symbol} exact price component coverage failed; missing={list(missing[:5])}")
    result["decision_at"] = result["time"] + pd.Timedelta(hours=1)

    metrics = metrics_raw.copy()
    metrics["oi_source_at"] = _time(metrics["create_time"])
    metrics["oi_available_at"] = metrics["oi_source_at"]
    metrics = metrics.rename(columns={
        "sum_open_interest": "oi_base",
        "sum_open_interest_value": "oi_usd",
        "count_toptrader_long_short_ratio": "toptrader_account_ratio",
        "sum_toptrader_long_short_ratio": "toptrader_position_ratio",
        "count_long_short_ratio": "account_long_short_ratio",
        "sum_taker_long_short_vol_ratio": "taker_long_short_ratio",
    }).sort_values("oi_available_at")
    oi_columns = [
        "oi_source_at", "oi_available_at", "oi_base", "oi_usd", "toptrader_account_ratio",
        "toptrader_position_ratio", "account_long_short_ratio", "taker_long_short_ratio",
    ]
    result = pd.merge_asof(
        result.sort_values("decision_at"), metrics[oi_columns], left_on="decision_at",
        right_on="oi_available_at", direction="backward", tolerance=pd.Timedelta(minutes=65),
    )
    result["oi_age_seconds"] = (result["decision_at"] - result["oi_available_at"]).dt.total_seconds()

    funding = funding_raw.copy()
    funding["funding_at"] = _time(funding["calc_time"])
    funding["funding_available_at"] = funding["funding_at"]
    funding = funding.rename(columns={"last_funding_rate": "funding_rate"}).sort_values("funding_available_at")
    funding_columns = ["funding_at", "funding_available_at", "funding_rate", "funding_interval_hours"]
    result = pd.merge_asof(
        result.sort_values("decision_at"), funding[funding_columns], left_on="decision_at",
        right_on="funding_available_at", direction="backward", tolerance=pd.Timedelta(hours=12),
    )
    result["funding_age_seconds"] = (result["decision_at"] - result["funding_available_at"]).dt.total_seconds()
    result["basis_absolute"] = result["close"].astype(float) - result["index_close"].astype(float)
    result["basis_pct"] = result["close"].astype(float) / result["index_close"].astype(float) - 1.0
    result["spot_basis_pct"] = result["close"].astype(float) / result["spot_close"].astype(float) - 1.0
    result["provider"] = "BINANCE_VISION_UM"
    result["provider_symbol"] = symbol
    result["local_alias"] = f"{symbol[:-4]}-USDT-SWAP"
    result["alignment_method"] = "EXACT_PRICE_BAR_BACKWARD_AVAILABILITY_ASOF"
    required = ["oi_usd", "oi_available_at", "funding_rate", "funding_available_at"]
    if result[required].isna().any().any():
        raise ValueError(f"{symbol} contains stale/missing required aligned observations")
    report = validate_derivatives_frame(
        result, timestamp_column="time",
        numeric_columns=["open", "high", "low", "close", "volume", "oi_base", "oi_usd", "funding_rate", "basis_pct"],
        expected_frequency=pd.Timedelta(hours=1), maximum_gap=pd.Timedelta(hours=1),
        minimum_rows=len(expected), nonnegative_columns=["open", "high", "low", "close", "volume", "oi_base", "oi_usd"],
    )
    if not report.valid:
        raise ValueError(f"{symbol} integrity failed: {report.status}/{report.code}")
    source_receipts = [item for group in receipts.values() for item in group]
    source_hash = hashlib.sha256("".join(item["sha256"] for item in sorted(source_receipts, key=lambda x: x["url"])).encode()).hexdigest()
    return result, {
        "symbol": symbol, "source_hash": source_hash, "source_files": len(source_receipts),
        "source_bytes": sum(item["bytes"] for item in source_receipts), "integrity": report.__dict__,
        "component_hashes": {
            name: hashlib.sha256("".join(item["sha256"] for item in group).encode()).hexdigest()
            for name, group in sorted(receipts.items())
        },
    }


def _write(frame: pd.DataFrame, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    serial = frame.copy()
    for column in serial.columns:
        if pd.api.types.is_datetime64_any_dtype(serial[column]):
            serial[column] = serial[column].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    serial.to_csv(path, index=False, lineterminator="\n", quoting=csv.QUOTE_MINIMAL, float_format="%.12g")
    payload = path.read_bytes()
    return {"path": path.as_posix(), "rows": len(serial), "sha256": _sha(payload), "bytes": len(payload)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    if not 1 <= args.workers <= 32:
        raise ValueError("workers must be between 1 and 32")
    manifest = {
        "schema": SCHEMA_VERSION, "provider": "BINANCE_VISION_UM", "timeframe": "1h",
        "requested_start": START.isoformat(), "requested_end": END.isoformat(),
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "outcomes_evaluated": False,
        "symbols": {}, "splits": {}, "blind_accessed": False,
    }
    ranges = {
        "DERIV_DEV": (DERIV_DEV_START, DERIV_DEV_END),
        "DERIV_VALIDATION_SEALED": (DERIV_VALIDATION_START, DERIV_VALIDATION_END),
        "DERIV_BLIND_SEALED": (DERIV_BLIND_START, DERIV_BLIND_END),
    }
    for symbol in SYMBOLS:
        frame, audit = materialize_symbol(symbol, args.workers)
        manifest["symbols"][symbol] = audit
        for role, (start, end) in ranges.items():
            split = frame[(frame["time"] >= start) & (frame["time"] <= end)].copy()
            receipt = _write(split, ROOT / "datasets" / role / f"{symbol}_1h.csv")
            manifest["splits"].setdefault(role, {})[symbol] = receipt
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()[:16]
    path = ROOT / f"materialization-{identity}.json"
    path.write_text(json.dumps(manifest | {"materialization_id": identity}, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

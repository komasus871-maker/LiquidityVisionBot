import asyncio
import time
from typing import Any

import aiohttp
import pandas as pd

from .base import MarketProvider


class OKXProvider(MarketProvider):
    """Public OKX USDT perpetual-swap market data provider."""

    BASE_URL = "https://www.okx.com"
    INSTRUMENT_TTL = 300

    def __init__(self) -> None:
        self._instrument_cache: dict[str, dict[str, Any]] = {}
        self._instrument_cache_at = 0.0
        self._instrument_lock = asyncio.Lock()

    @staticmethod
    def _normalize_base(symbol: str) -> str:
        value = (symbol or "").upper().strip().replace("/", "-").replace("_", "-")
        if value.endswith("-USDT-SWAP"):
            return value[: -len("-USDT-SWAP")]
        if value.endswith("USDT-SWAP"):
            return value[: -len("USDT-SWAP")].rstrip("-")
        if value.endswith("-USDT"):
            return value[: -len("-USDT")]
        if value.endswith("USDT"):
            return value[: -len("USDT")].rstrip("-")
        if value.endswith("-PERP"):
            return value[: -len("-PERP")]
        return value

    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=20)
        headers = {"User-Agent": "LiquidityVision/4.0"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(f"{self.BASE_URL}{path}", params=params) as response:
                if response.status != 200:
                    text = await response.text()
                    raise RuntimeError(f"OKX API HTTP {response.status}: {text[:180]}")
                payload = await response.json()

        if payload.get("code") != "0":
            raise RuntimeError(payload.get("msg") or "Unknown OKX API error")
        return payload

    async def _load_swap_instruments(self, force: bool = False) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        if (
            not force
            and self._instrument_cache
            and now - self._instrument_cache_at < self.INSTRUMENT_TTL
        ):
            return self._instrument_cache

        async with self._instrument_lock:
            now = time.monotonic()
            if (
                not force
                and self._instrument_cache
                and now - self._instrument_cache_at < self.INSTRUMENT_TTL
            ):
                return self._instrument_cache

            payload = await self._request(
                "/api/v5/public/instruments",
                {"instType": "SWAP"},
            )
            instruments: dict[str, dict[str, Any]] = {}
            for item in payload.get("data", []):
                inst_id = str(item.get("instId", "")).upper()
                if (
                    inst_id.endswith("-USDT-SWAP")
                    and item.get("state") in {"live", "preopen"}
                ):
                    instruments[inst_id] = item

            if not instruments:
                raise RuntimeError("OKX returned no active USDT swap instruments")

            self._instrument_cache = instruments
            self._instrument_cache_at = time.monotonic()
            return instruments

    async def resolve_instrument(self, symbol: str) -> dict[str, Any]:
        base = self._normalize_base(symbol)
        candidate = f"{base}-USDT-SWAP"
        instruments = await self._load_swap_instruments()
        instrument = instruments.get(candidate)
        if instrument is None:
            # Refresh once to cover new listings or recently changed instrument states.
            instruments = await self._load_swap_instruments(force=True)
            instrument = instruments.get(candidate)
        if instrument is None:
            raise ValueError(f"OKX perpetual swap {candidate} was not found")
        return instrument

    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        instrument = await self.resolve_instrument(symbol)
        inst_id = instrument["instId"]
        payload = await self._request(
            "/api/v5/market/ticker",
            {"instId": inst_id},
        )
        data = payload.get("data", [])
        if not data:
            raise RuntimeError(f"OKX returned no ticker for {inst_id}")
        ticker = data[0]
        last = float(ticker.get("last") or 0)
        open_24h = float(ticker.get("open24h") or 0)
        change_percent = ((last - open_24h) / open_24h * 100) if open_24h else 0.0
        return {
            "exchange": "OKX",
            "instrument_id": inst_id,
            "last": last,
            "change_percent_24h": change_percent,
            "high_24h": float(ticker.get("high24h") or 0),
            "low_24h": float(ticker.get("low24h") or 0),
            "volume_contracts_24h": float(ticker.get("vol24h") or 0),
            "volume_currency_24h": float(ticker.get("volCcy24h") or 0),
            "timestamp": ticker.get("ts"),
        }

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
    ) -> pd.DataFrame:
        instrument = await self.resolve_instrument(symbol)
        inst_id = instrument["instId"]

        interval_map = {
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1H",
            "2h": "2H",
            "4h": "4H",
            "6h": "6H",
            "12h": "12H",
            "1d": "1D",
            "1w": "1W",
        }
        bar = interval_map.get(interval.lower())
        if bar is None:
            raise ValueError(f"Unsupported OKX timeframe: {interval}")

        requested = max(50, min(int(limit), 1000))
        rows: list[list[str]] = []
        after: str | None = None

        while len(rows) < requested:
            batch_limit = min(300, requested - len(rows))
            params: dict[str, Any] = {
                "instId": inst_id,
                "bar": bar,
                "limit": str(batch_limit),
            }
            if after:
                params["after"] = after

            payload = await self._request("/api/v5/market/history-candles", params)
            batch = payload.get("data", [])
            if not batch:
                break

            rows.extend(batch)
            oldest_ts = batch[-1][0]
            if after == oldest_ts or len(batch) < batch_limit:
                break
            after = oldest_ts

        if not rows:
            raise RuntimeError(f"OKX returned no candles for {inst_id}")

        # OKX returns newest first. Deduplicate pages and restore chronological order.
        unique_by_ts = {row[0]: row for row in rows}
        candles = sorted(unique_by_ts.values(), key=lambda row: int(row[0]))[-requested:]

        df = pd.DataFrame(
            candles,
            columns=[
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "volCcy",
                "volCcyQuote",
                "confirm",
            ],
        )
        df["time"] = pd.to_datetime(df["time"].astype("int64"), unit="ms", utc=True)
        for column in ("open", "high", "low", "close", "volume"):
            df[column] = pd.to_numeric(df[column], errors="raise")

        # Keep metadata for diagnostics without changing analyzer columns.
        df.attrs["exchange"] = "OKX"
        df.attrs["instrument_id"] = inst_id
        df.attrs["instrument_type"] = "SWAP"
        df.attrs["tick_size"] = instrument.get("tickSz")
        return df

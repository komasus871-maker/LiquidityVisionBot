from services.cache import cache
from services.data_integrity import DataIntegrityEngine
from services.providers.okx import OKXProvider
from utils.timeframe import normalize_market_timeframe
import pandas as pd


class Market:

    def __init__(self):

        self.provider = OKXProvider()
        self.integrity = DataIntegrityEngine()

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500
    ):

        canonical_interval = normalize_market_timeframe(interval)
        normalized_symbol = str(symbol).upper()
        if canonical_interval is None:
            unknown_frame = DataIntegrityEngine.provider_error_frame(
                provider=type(self.provider).__name__, symbol=normalized_symbol,
                timeframe=str(interval), reason="UNKNOWN_TIMEFRAME",
            )
            # This is a caller-contract error, not a provider outage.
            unknown_frame.attrs.pop("market_data_provider_error", None)
            return self.integrity.prepare_market_frame(
                unknown_frame, timeframe=str(interval), minimum_history=1, require_freshness=True,
            ).frame
        cache_key = f"{type(self.provider).__name__}:{normalized_symbol}:{canonical_interval}:{int(limit)}"

        cached = cache.get(cache_key)

        if cached is not None:
            frame = cached.copy(deep=True)
            frame.attrs["market_data_cache_hit"] = True
        else:
            try:
                frame = await self.provider.get_klines(
                    symbol=normalized_symbol,
                    interval=canonical_interval,
                    limit=limit,
                )
            except Exception as exc:
                error_frame = DataIntegrityEngine.provider_error_frame(
                    provider=type(self.provider).__name__, symbol=normalized_symbol,
                    timeframe=canonical_interval, reason=f"{type(exc).__name__}: {exc}",
                )
                return self.integrity.prepare_market_frame(
                    error_frame, timeframe=canonical_interval, minimum_history=1, require_freshness=True,
                ).frame
            if not isinstance(frame, pd.DataFrame):
                error_frame = DataIntegrityEngine.provider_error_frame(
                    provider=type(self.provider).__name__, symbol=normalized_symbol,
                    timeframe=canonical_interval, reason="Provider returned a non-DataFrame candle payload",
                )
                return self.integrity.prepare_market_frame(
                    error_frame, timeframe=canonical_interval, minimum_history=1, require_freshness=True,
                ).frame
            frame = frame.copy(deep=True)
            frame.attrs.update({
                "market_data_provider": frame.attrs.get("exchange") or type(self.provider).__name__,
                "market_data_symbol": normalized_symbol,
                "market_data_timeframe": canonical_interval,
                "market_data_requested_limit": int(limit),
                "market_data_fallback_used": False,
                "market_data_cache_hit": False,
            })
            # Keep raw provider chronology until this one shared boundary has
            # recorded duplicate/order diagnostics; never cache a bad sequence.
            validated = self.integrity.prepare_market_frame(
                frame, timeframe=canonical_interval, minimum_history=1, require_freshness=True,
            )
            frame = validated.frame
            if validated.valid:
                cache.set(cache_key, frame.copy(deep=True), ttl=20)

        # Re-evaluate timestamps after cache retrieval. A successful cache hit
        # is not evidence that those candles are still fresh.
        return self.integrity.prepare_market_frame(
            frame, timeframe=canonical_interval, minimum_history=1, require_freshness=True,
        ).frame

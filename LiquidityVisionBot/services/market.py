from services.cache import cache
from services.providers.okx import OKXProvider


class Market:

    def __init__(self):

        self.provider = OKXProvider()

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500
    ):

        cache_key = f"{symbol}_{interval}_{limit}"

        cached = cache.get(cache_key)

        if cached is not None:
            return cached

        df = await self.provider.get_klines(
            symbol=symbol,
            interval=interval,
            limit=limit
        )

        cache.set(
            cache_key,
            df,
            ttl=20
        )

        return df
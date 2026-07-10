import aiohttp


class Futures:

    BASE_URL = "https://fapi.binance.com"

    async def funding_rate(self, symbol: str):

        symbol = symbol.upper()

        if not symbol.endswith("USDT"):
            symbol += "USDT"

        url = (
            f"{self.BASE_URL}"
            f"/fapi/v1/premiumIndex"
            f"?symbol={symbol}"
        )

        async with aiohttp.ClientSession() as session:

            async with session.get(url) as response:

                return await response.json()

    async def open_interest(self, symbol: str):

        symbol = symbol.upper()

        if not symbol.endswith("USDT"):
            symbol += "USDT"

        url = (
            f"{self.BASE_URL}"
            f"/fapi/v1/openInterest"
            f"?symbol={symbol}"
        )

        async with aiohttp.ClientSession() as session:

            async with session.get(url) as response:

                return await response.json()

    async def long_short_ratio(self, symbol: str):

        symbol = symbol.upper()

        if not symbol.endswith("USDT"):
            symbol += "USDT"

        url = (
            f"{self.BASE_URL}"
            f"/futures/data/globalLongShortAccountRatio"
            f"?symbol={symbol}"
            f"&period=1h"
            f"&limit=1"
        )

        async with aiohttp.ClientSession() as session:

            async with session.get(url) as response:

                data = await response.json()

        if isinstance(data, list) and len(data):

            return data[0]

        return None
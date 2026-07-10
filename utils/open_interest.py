import aiohttp


class OpenInterest:

    async def get(self, symbol):

        if not symbol.endswith("USDT"):
            symbol += "USDT"

        url = (
            "https://fapi.binance.com"
            "/fapi/v1/openInterest"
            f"?symbol={symbol}"
        )

        async with aiohttp.ClientSession() as session:

            async with session.get(url) as response:

                data = await response.json()

        return float(data["openInterest"])
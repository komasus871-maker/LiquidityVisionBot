import aiohttp


class Funding:

    async def get(self, symbol):

        if not symbol.endswith("USDT"):
            symbol += "USDT"

        url = (
            "https://fapi.binance.com"
            "/fapi/v1/premiumIndex"
            f"?symbol={symbol}"
        )

        async with aiohttp.ClientSession() as session:

            async with session.get(url) as response:

                data = await response.json()

        return float(data["lastFundingRate"])

    async def analyze(self, symbol):

        funding = await self.get(symbol)

        if funding < 0:

            return "🟢 Negative"

        if funding > 0:

            return "🔴 Positive"

        return "➖ Neutral"
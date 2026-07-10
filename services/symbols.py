import aiohttp


class Symbols:

    URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"

    async def futures(self):

        async with aiohttp.ClientSession() as session:

            async with session.get(self.URL) as response:

                data = await response.json()

        symbols = []

        for symbol in data["symbols"]:

            if (
                symbol["contractType"] == "PERPETUAL"
                and symbol["quoteAsset"] == "USDT"
                and symbol["status"] == "TRADING"
            ):
                symbols.append(symbol["symbol"])

        return sorted(symbols)
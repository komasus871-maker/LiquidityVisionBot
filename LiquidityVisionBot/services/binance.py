import aiohttp


BASE_URL = "https://api.binance.com"


async def get_price(symbol: str):
    symbol = symbol.upper() + "USDT"

    url = f"{BASE_URL}/api/v3/ticker/24hr?symbol={symbol}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.json()
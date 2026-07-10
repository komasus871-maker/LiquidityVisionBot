import aiohttp
import pandas as pd

from .base import MarketProvider


class OKXProvider(MarketProvider):

    BASE_URL = "https://www.okx.com"

    async def get_klines(
        self,
        symbol,
        interval="1H",
        limit=500
    ):

        symbol = symbol.upper()

        if not symbol.endswith("USDT"):
            symbol += "-USDT"

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
            "1w": "1W"
        }

        interval = interval_map.get(interval, "1H")

        url = (
            f"{self.BASE_URL}/api/v5/market/history-candles"
            f"?instId={symbol}"
            f"&bar={interval}"
            f"&limit={limit}"
        )

        async with aiohttp.ClientSession() as session:

            async with session.get(url) as response:

                if response.status != 200:

                    raise Exception(
                        f"OKX API Error {response.status}"
                    )

                data = await response.json()

        if data["code"] != "0":

            raise Exception(data["msg"])

        candles = data["data"]

        candles.reverse()

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
                "confirm"
            ]
        )

        df["time"] = pd.to_datetime(
            df["time"].astype(int),
            unit="ms"
        )

        numeric = [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        for col in numeric:
            df[col] = df[col].astype(float)

        return df
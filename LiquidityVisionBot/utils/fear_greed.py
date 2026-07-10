import aiohttp


class FearGreed:

    URL = "https://api.alternative.me/fng/"

    async def get(self):

        async with aiohttp.ClientSession() as session:

            async with session.get(self.URL) as response:

                data = await response.json()

        value = int(data["data"][0]["value"])

        text = data["data"][0]["value_classification"]

        return value, text
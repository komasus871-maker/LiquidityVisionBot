import aiohttp


class HTTPSession:

    session = None

    @classmethod
    async def get(cls):

        if cls.session is None:

            cls.session = aiohttp.ClientSession()

        return cls.session

    @classmethod
    async def close(cls):

        if cls.session:

            await cls.session.close()

            cls.session = None
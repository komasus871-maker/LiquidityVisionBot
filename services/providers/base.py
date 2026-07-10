from abc import ABC, abstractmethod


class MarketProvider(ABC):

    @abstractmethod
    async def get_klines(
        self,
        symbol,
        interval,
        limit
    ):
        pass
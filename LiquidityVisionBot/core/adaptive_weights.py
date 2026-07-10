from core.weights import WEIGHTS
from core.market_phase import MarketPhase


class AdaptiveWeights:

    def get(

        self,

        phase,

        volatility,

        timeframe

    ):

        weights = WEIGHTS.copy()

        if phase == MarketPhase.EXPANSION:

            weights["bos"] += 5
            weights["trend"] += 5
            weights["displacement"] += 5
            weights["fvg"] += 3

        elif phase == MarketPhase.REVERSAL:

            weights["choch"] += 8
            weights["breaker"] += 5
            weights["mitigation"] += 5

        elif phase == MarketPhase.CONSOLIDATION:

            weights["bos"] -= 8
            weights["trend"] -= 5
            weights["liquidity"] += 5
            weights["sweep"] += 5

        elif phase == MarketPhase.ACCUMULATION:

            weights["discount"] = 15
            weights["order_block"] += 5
            weights["fvg"] += 5

        elif phase == MarketPhase.DISTRIBUTION:

            weights["premium"] = 15
            weights["breaker"] += 5

        if volatility > 2:

            weights["displacement"] += 3
            weights["volume"] += 3

        if timeframe in ("1m", "3m", "5m"):

            weights["volume"] += 2
            weights["trend"] -= 2

        elif timeframe in ("4h", "1d"):

            weights["trend"] += 5
            weights["structure"] += 5

        return weights
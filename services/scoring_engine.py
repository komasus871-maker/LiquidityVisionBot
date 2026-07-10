class ScoringEngine:

    def score(self, signals):

        bull = 0
        bear = 0

        if signals["trend"]:
            bull += 15
        else:
            bear += 15

        if signals["structure"]:
            bull += 15
        else:
            bear += 15

        if signals["bos"]:
            bull += 15

        if signals["choch"]:
            bull += 10

        if signals["order_block"]:
            bull += 10

        if signals["breaker"]:
            bull += 8

        if signals["mitigation"]:
            bear += 8

        if signals["fvg"]:
            bull += 10

        if signals["discount"]:
            bull += 10
        else:
            bear += 10

        if signals["volume"]:
            bull += 5

        if signals["momentum"]:
            bull += 5

        return bull, bear
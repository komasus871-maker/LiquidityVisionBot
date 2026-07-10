class TimeframeScore:

    @staticmethod
    def calculate(results):

        score = 0

        for tf in results.values():

            if "Bullish" in tf:

                score += 20

        return score
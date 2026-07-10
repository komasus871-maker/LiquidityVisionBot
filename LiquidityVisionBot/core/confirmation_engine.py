class ConfirmationEngine:

    def check(

        self,

        analysis

    ):

        confirmations = 0

        if "Bullish" in analysis["trend"]:

            confirmations += 1

        if "Bullish" in analysis["bos"]:

            confirmations += 1

        if "Bullish" in analysis["breaker"]:

            confirmations += 1

        if "Bullish" in analysis["fvg"]:

            confirmations += 1

        if "Discount" in analysis["premium"]["zone"]:

            confirmations += 1

        if "Spike" in analysis["volume"]:

            confirmations += 1

        return {

            "count": confirmations,

            "passed": confirmations >= 4

        }
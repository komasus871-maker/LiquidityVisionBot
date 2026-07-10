class HierarchyEngine:

    TIMEFRAMES = (

        "1D",

        "4H",

        "1H",

        "15M",

        "5M"

    )

    def analyze(

        self,

        analyses

    ):

        hierarchy = {}

        score = 0

        direction = None

        aligned = 0

        for tf in self.TIMEFRAMES:

            analysis = analyses[tf]

            trend = analysis["trend"]

            hierarchy[tf] = trend

            if direction is None:

                direction = trend

            if trend == direction:

                aligned += 1

        score = aligned * 20

        return {

            "direction": direction,

            "aligned": aligned,

            "score": score,

            "hierarchy": hierarchy

        }
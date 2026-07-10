class MTFEngine:

    def analyze(

        self,

        daily,

        h4,

        h1,

        m15

    ):

        score = 0

        aligned = 0

        trends = [

            daily,

            h4,

            h1,

            m15

        ]

        bull = trends.count(

            "🟢 Bullish"

        )

        bear = trends.count(

            "🔴 Bearish"

        )

        aligned = max(

            bull,

            bear

        )

        score = aligned * 25

        return {

            "bull": bull,

            "bear": bear,

            "aligned": aligned,

            "score": score

        }
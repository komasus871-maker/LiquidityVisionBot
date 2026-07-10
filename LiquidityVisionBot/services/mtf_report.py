class MTFReport:

    def build(

        self,

        mtf,

        probability

    ):

        text = ""

        for tf in [

            "15m",

            "1h",

            "4h",

            "1d"

        ]:

            text += f"""

🕒 {tf.upper()}

{mtf[tf]["trend"]}

Confidence

{mtf[tf]["confidence"]}%

"""

        if probability >= 80:

            overall = "🟢 STRONG LONG"

        elif probability >= 60:

            overall = "🟢 LONG"

        elif probability >= 40:

            overall = "🟡 NEUTRAL"

        else:

            overall = "🔴 SHORT"

        return f"""

🌍 <b>Multi Timeframe Analysis</b>

━━━━━━━━━━━━━━━━━━

{text}

━━━━━━━━━━━━━━━━━━

🎯 Overall Probability

{probability}%

━━━━━━━━━━━━━━━━━━

🚀 Overall Bias

{overall}

"""
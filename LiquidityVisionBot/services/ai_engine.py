from utils.status import market_status


class AIEngine:

    def explain(self, analysis):

        text = []

        score = analysis["score"]

        text.append(
            f"Status: {market_status(score)}"
        )

        if analysis["trend"] == "Bullish":

            text.append(
                "• Price above EMA200."
            )

        else:

            text.append(
                "• Price below EMA200."
            )

        if analysis["rsi"] < 30:

            text.append(
                "• RSI indicates oversold."
            )

        elif analysis["rsi"] > 70:

            text.append(
                "• RSI indicates overbought."
            )

        else:

            text.append(
                "• RSI is healthy."
            )

        if analysis["funding"] < 0:

            text.append(
                "• Funding is negative (bullish)."
            )

        else:

            text.append(
                "• Funding is positive."
            )

        if analysis["long_short"] > 1:

            text.append(
                "• Longs dominate."
            )

        else:

            text.append(
                "• Shorts dominate."
            )

        if score >= 90:

            recommendation = "🔥 Strong LONG"

        elif score >= 75:

            recommendation = "🟢 LONG"

        elif score >= 60:

            recommendation = "🟡 WAIT"

        elif score >= 40:

            recommendation = "🟠 SHORT"

        else:

            recommendation = "🔴 Strong SHORT"

        return {

            "recommendation": recommendation,

            "summary": "\n".join(text)

        }
class Explainer:

    def explain(data):

        text = []

        if data["trend"] == "bullish":

            text.append(
                "Long-term trend remains bullish."
            )

        if data["bos"] == "bullish":

            text.append(
                "Bullish Break of Structure detected."
            )

        if data["macd"] == "bearish":

            text.append(
                "Momentum is weakening."
            )

        return "\n".join(text)
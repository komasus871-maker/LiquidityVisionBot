from core.models import Signal


class SignalBuilder:

    def build(self, analysis):

        signals = []

        signals.append(

            Signal(

                name="trend",

                direction=(
                    "bullish"
                    if "Bullish" in analysis["trend"]
                    else "bearish"
                ),

                strength=100,

                confidence=95,

                active=True

            )

        )

        signals.append(

            Signal(

                name="structure",

                direction=(
                    "bullish"
                    if "Bullish" in analysis["structure"]
                    else
                    "bearish"
                    if "Bearish" in analysis["structure"]
                    else
                    "neutral"
                ),

                strength=90,

                confidence=85,

                active=True

            )

        )

        signals.append(

            Signal(

                name="bos",

                direction=(
                    "bullish"
                    if "Bullish" in analysis["bos"]
                    else
                    "bearish"
                    if "Bearish" in analysis["bos"]
                    else
                    "neutral"
                ),

                strength=95,

                confidence=90,

                active="No BOS" not in analysis["bos"]

            )

        )

        signals.append(

            Signal(

                name="choch",

                direction=(
                    "bullish"
                    if "Bullish" in analysis["choch"]
                    else
                    "bearish"
                    if "Bearish" in analysis["choch"]
                    else
                    "neutral"
                ),

                strength=90,

                confidence=88,

                active="No CHOCH" not in analysis["choch"]

            )

        )

        signals.append(

            Signal(

                name="fvg",

                direction=(
                    "bullish"
                    if "Bullish" in analysis["fvg"]
                    else
                    "bearish"
                    if "Bearish" in analysis["fvg"]
                    else
                    "neutral"
                ),

                strength=80,

                confidence=80,

                active="No" not in analysis["fvg"]

            )

        )

        return signals
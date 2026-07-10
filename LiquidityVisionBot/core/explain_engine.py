class ExplainEngine:

    def build(

        self,

        analysis,

        decision,

        probability,

        quality,

        pattern,

        session,

        context

    ):

        reasons = []

        if "Bullish" in analysis["trend"]:

            reasons.append(

                "Higher timeframe trend supports buyers."

            )

        else:

            reasons.append(

                "Higher timeframe trend supports sellers."

            )

        reasons.extend(

            context["notes"]

        )

        if pattern["samples"] >= 20:

            reasons.append(

                f"{pattern['samples']} similar setups found."

            )

            reasons.append(

                f"Historical Win Rate {pattern['winrate']}%."

            )

        reasons.append(

            f"Trading Session: {session['session']}."

        )

        reasons.append(

            f"Confidence: {probability}%."

        )

        reasons.append(

            f"Setup Quality: {quality['stars']}."

        )

        return reasons
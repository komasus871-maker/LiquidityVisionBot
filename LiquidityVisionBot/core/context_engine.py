from core.market_phase import MarketPhase


class ContextEngine:

    def analyze(

        self,

        market

    ):

        phase = MarketPhase().detect(

            market["trend"],

            market["structure"],

            market["bos"],

            market["choch"],

            market["premium"],

            market["displacement"],

            market["volume"]

        )

        score = 0

        notes = []

        if phase == MarketPhase.EXPANSION:

            score += 15

            notes.append("Expansion")

        elif phase == MarketPhase.REVERSAL:

            score -= 5

            notes.append("Reversal")

        elif phase == MarketPhase.CONSOLIDATION:

            score -= 10

            notes.append("Consolidation")

        elif phase == MarketPhase.ACCUMULATION:

            score += 10

            notes.append("Accumulation")

        elif phase == MarketPhase.DISTRIBUTION:

            score -= 10

            notes.append("Distribution")

        return {

            "phase": phase,

            "score": score,

            "notes": notes

        }
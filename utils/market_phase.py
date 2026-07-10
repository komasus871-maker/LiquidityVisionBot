class MarketPhase:

    def __init__(self, score):

        self.score = score

    def analyze(self):

        if self.score >= 90:

            return "🚀 Expansion"

        if self.score >= 70:

            return "📈 Trend"

        if self.score >= 50:

            return "📊 Range"

        return "📉 Distribution"
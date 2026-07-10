class PremiumDiscount:
    """Dealing-range location based on a recent, self-consistent range."""

    def __init__(self, df, lookback: int = 80):
        self.df = df
        self.lookback = min(lookback, len(df))

    def _range(self):
        window = self.df.tail(self.lookback)
        high = float(window["high"].max())
        low = float(window["low"].min())
        return high, low

    def swing_high(self):
        return self._range()[0]

    def swing_low(self):
        return self._range()[1]

    def equilibrium(self):
        high, low = self._range()
        return (high + low) / 2

    def premium_percent(self):
        high, low = self._range()
        price = float(self.df["close"].iloc[-1])
        size = high - low
        if size <= 0:
            return 50.0
        return round(max(0.0, min(100.0, ((price - low) / size) * 100)), 2)

    def zone(self):
        pct = self.premium_percent()
        if pct >= 62:
            return "🔴 Premium"
        if pct <= 38:
            return "🟢 Discount"
        return "🟡 Equilibrium"

    def analyze(self):
        high, low = self._range()
        return {
            "zone": self.zone(),
            "equilibrium": round((high + low) / 2, 2),
            "premium": self.premium_percent(),
            "high": round(high, 2),
            "low": round(low, 2),
        }

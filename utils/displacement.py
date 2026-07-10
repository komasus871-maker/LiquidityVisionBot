class Displacement:
    """Measures candle expansion using body efficiency and body-vs-average."""

    def __init__(self, df):
        self.df = df

    @staticmethod
    def body(candle):
        return abs(float(candle["close"]) - float(candle["open"]))

    @staticmethod
    def candle_range(candle):
        return max(float(candle["high"]) - float(candle["low"]), 0.0)

    def average_body(self, period=20):
        candles = self.df.tail(period)
        values = [self.body(c) for _, c in candles.iterrows()]
        return sum(values) / len(values) if values else 0.0

    def metrics(self):
        candle = self.df.iloc[-1]
        body = self.body(candle)
        rng = self.candle_range(candle)
        avg = self.average_body()
        efficiency = body / rng * 100 if rng else 0.0
        expansion = body / avg if avg else 0.0
        direction = "Bullish" if candle["close"] > candle["open"] else "Bearish" if candle["close"] < candle["open"] else "Neutral"
        return direction, round(efficiency, 2), round(expansion, 2)

    def analyze(self):
        direction, efficiency, expansion = self.metrics()
        composite = min(100.0, efficiency * 0.65 + min(expansion / 2.0, 1.0) * 100 * 0.35)

        if direction == "Neutral":
            return "⚪ Weak Displacement (0.0%, 0.0x)"
        if composite >= 72 and expansion >= 1.35:
            level = "Strong"
            icon = "🟢" if direction == "Bullish" else "🔴"
        elif composite >= 48 and expansion >= 0.9:
            level = "Moderate"
            icon = "🟡"
        else:
            level = "Weak"
            icon = "⚪"

        return f"{icon} {level} {direction} Displacement ({efficiency}%, {expansion}x)"

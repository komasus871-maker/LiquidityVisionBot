class VolumeProfile:
    """Relative volume based on the latest confirmed candle versus prior confirmed candles."""

    def __init__(self, df):
        self.df = df

    def average_volume(self, period=20):
        history = self.df["volume"].iloc[-period-1:-1]
        if history.empty:
            history = self.df["volume"].iloc[:-1]
        return float(history.mean()) if not history.empty else 0.0

    def current_volume(self):
        return float(self.df["volume"].iloc[-1])

    def relative_volume(self):
        avg = self.average_volume()
        return round(self.current_volume() / avg, 2) if avg > 0 else 1.0

    def analyze(self):
        rv = self.relative_volume()
        candle = self.df.iloc[-1]
        body = abs(float(candle["close"]) - float(candle["open"]))
        rng = max(float(candle["high"]) - float(candle["low"]), 0.0)
        efficiency = body / rng if rng else 0.0
        if rv >= 2.0 and efficiency < 0.35:
            return f"🔴 Volume Climax ({rv}x)"
        if rv >= 2.0:
            return f"🟢 Volume Spike ({rv}x)"
        if rv >= 1.25:
            return f"🟢 Elevated Volume ({rv}x)"
        if rv < 0.55:
            return f"⚪ Low Volume ({rv}x)"
        return f"⚪ Normal Volume ({rv}x)"

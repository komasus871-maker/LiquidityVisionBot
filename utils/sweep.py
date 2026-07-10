class Sweep:

    def __init__(self, df):

        self.df = df

    def buy_side(self):

        highs = self.df["high"].values

        closes = self.df["close"].values

        if len(highs) < 5:
            return None

        level = max(highs[:-1])

        last_high = highs[-1]
        last_close = closes[-1]

        if last_high > level and last_close < level:

            return {

                "type": "buy",

                "level": float(level),

                "high": float(last_high),

                "close": float(last_close)

            }

        return None

    def sell_side(self):

        lows = self.df["low"].values

        closes = self.df["close"].values

        if len(lows) < 5:
            return None

        level = min(lows[:-1])

        last_low = lows[-1]
        last_close = closes[-1]

        if last_low < level and last_close > level:

            return {

                "type": "sell",

                "level": float(level),

                "low": float(last_low),

                "close": float(last_close)

            }

        return None

    def internal(self):

        highs = self.df["high"].values[-20:]

        lows = self.df["low"].values[-20:]

        close = float(self.df["close"].iloc[-1])

        high = max(highs)
        low = min(lows)

        eq = (high + low) / 2

        if close > eq:

            return {

                "bias": "bullish",

                "equilibrium": eq

            }

        return {

            "bias": "bearish",

            "equilibrium": eq

        }

    def stop_hunt(self):

        buy = self.buy_side()

        if buy:

            return "🔴 Buy Side Stop Hunt"

        sell = self.sell_side()

        if sell:

            return "🟢 Sell Side Stop Hunt"

        return None

    def fake_breakout(self):

        highs = self.df["high"].values

        lows = self.df["low"].values

        closes = self.df["close"].values

        if len(highs) < 10:

            return None

        max_high = max(highs[:-1])

        min_low = min(lows[:-1])

        if highs[-1] > max_high and closes[-1] < max_high:

            return "🔴 Fake Bullish Breakout"

        if lows[-1] < min_low and closes[-1] > min_low:

            return "🟢 Fake Bearish Breakout"

        return None

    def analyze(self):

        stop = self.stop_hunt()

        if stop:

            return stop

        fake = self.fake_breakout()

        if fake:

            return fake

        buy = self.buy_side()

        if buy:

            return (

                f"🔴 Buy Side Sweep "

                f"({buy['level']:.2f})"

            )

        sell = self.sell_side()

        if sell:

            return (

                f"🟢 Sell Side Sweep "

                f"({sell['level']:.2f})"

            )

        internal = self.internal()

        return (

            f"⚪ Internal Liquidity "

            f"({internal['bias']})"

        )
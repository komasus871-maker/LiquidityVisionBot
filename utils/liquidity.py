class Liquidity:

    def __init__(self, df):

        self.df = df

    def equal_highs(self, tolerance=0.002):

        highs = self.df["high"].values

        if len(highs) < 10:
            return None

        last = highs[-2]

        for high in highs[-10:-2]:

            if abs(high - last) / last <= tolerance:
                return last

        return None

    def equal_lows(self, tolerance=0.002):

        lows = self.df["low"].values

        if len(lows) < 10:
            return None

        last = lows[-2]

        for low in lows[-10:-2]:

            if abs(low - last) / last <= tolerance:
                return last

        return None

    def buy_side_liquidity(self):

        high = self.equal_highs()

        if high is None:
            return None

        close = float(self.df["close"].iloc[-1])

        if close < high:
            return high

        return None

    def sell_side_liquidity(self):

        low = self.equal_lows()

        if low is None:
            return None

        close = float(self.df["close"].iloc[-1])

        if close > low:
            return low

        return None

    def buy_side_sweep(self):

        high = self.equal_highs()

        if high is None:
            return False

        candle = self.df.iloc[-1]

        return (
            candle["high"] > high
            and
            candle["close"] < high
        )

    def sell_side_sweep(self):

        low = self.equal_lows()

        if low is None:
            return False

        candle = self.df.iloc[-1]

        return (
            candle["low"] < low
            and
            candle["close"] > low
        )

    def internal_liquidity(self):

        highs = self.df["high"].values[-20:]
        lows = self.df["low"].values[-20:]

        return (
            max(highs) + min(lows)
        ) / 2

    def external_liquidity(self):

        highs = self.df["high"].values
        lows = self.df["low"].values

        return {

            "high": float(max(highs)),
            "low": float(min(lows))

        }

    def analyze(self):

        if self.sell_side_sweep():

            return "🟢 Sell Side Sweep"

        if self.buy_side_sweep():

            return "🔴 Buy Side Sweep"

        eql = self.equal_lows()

        if eql is not None:

            return f"🟢 Equal Lows ({eql:.2f})"

        eqh = self.equal_highs()

        if eqh is not None:

            return f"🔴 Equal Highs ({eqh:.2f})"

        return "⚪ No Liquidity"
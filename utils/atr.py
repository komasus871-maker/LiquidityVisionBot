import pandas as pd


class ATR:
    def __init__(self, df, period=14):
        self.df = df
        self.period = period

    def calculate(self):
        high = self.df["high"]
        low = self.df["low"]
        close = self.df["close"]
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        value = float(tr.rolling(self.period).mean().iloc[-1])
        return 0.0 if pd.isna(value) else value

    def analyze(self):
        price = float(self.df["close"].iloc[-1])
        atr = self.calculate()
        # Prevent microscopic stops on low-priced assets and unstable RR geometry.
        risk_distance = max(atr * 1.5, price * 0.0035)
        reward_unit = max(atr * 2.0, risk_distance * 1.20)
        return {
            "atr": atr,
            "risk_distance": risk_distance,
            "long_stop": price - risk_distance,
            "short_stop": price + risk_distance,
            "long_tp": (price + reward_unit, price + reward_unit * 2, price + reward_unit * 3),
            "short_tp": (price - reward_unit, price - reward_unit * 2, price - reward_unit * 3),
        }

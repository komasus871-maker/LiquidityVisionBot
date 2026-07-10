import pandas as pd


class SmartMoney:

    @staticmethod
    def equal_highs(df: pd.DataFrame):

        highs = df["high"].tail(10).tolist()

        result = []

        for i in range(len(highs)-1):

            if abs(highs[i]-highs[i+1]) < highs[i]*0.001:

                result.append(highs[i])

        return result

    @staticmethod
    def equal_lows(df):

        lows = df["low"].tail(10).tolist()

        result = []

        for i in range(len(lows)-1):

            if abs(lows[i]-lows[i+1]) < lows[i]*0.001:

                result.append(lows[i])

        return result

    @staticmethod
    def liquidity(df):

        highs = SmartMoney.equal_highs(df)

        lows = SmartMoney.equal_lows(df)

        return {

            "equal_highs": highs,

            "equal_lows": lows,

            "high_count": len(highs),

            "low_count": len(lows)

        }
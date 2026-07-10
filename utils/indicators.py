from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator


def ema(df, period):
    return EMAIndicator(
        close=df["close"],
        window=period
    ).ema_indicator()


def rsi(df, period=14):
    return RSIIndicator(
        close=df["close"],
        window=period
    ).rsi()


def macd(df):
    indicator = MACD(df["close"])

    return (
        indicator.macd(),
        indicator.macd_signal()
    )
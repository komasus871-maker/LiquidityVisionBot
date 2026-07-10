from dataclasses import dataclass


@dataclass
class Analysis:

    symbol: str

    price: float

    trend: str

    trend_strength: str

    bos: str

    choch: str

    liquidity: str

    sweep: str

    order_block: str

    fvg: str

    ema50: float

    ema200: float

    rsi: float

    macd: str

    volume: str

    atr: float

    entry: float

    stop: float

    tp1: float

    tp2: float

    tp3: float

    rr: float

    score: int

    probability: int

    recommendation: str
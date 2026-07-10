from dataclasses import dataclass


@dataclass
class Trade:

    entry: float

    stop: float

    tp1: float

    tp2: float

    tp3: float

    rr: float

    recommendation: str

    confidence: float
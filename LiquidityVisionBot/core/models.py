from dataclasses import dataclass, field
from typing import Any


@dataclass
class Signal:

    name: str

    direction: str

    strength: float

    confidence: float

    active: bool

    weight: float

    reason: str

    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Score:

    bull: float

    bear: float

    difference: float

    reasons: list[str]

    details: list[dict]


@dataclass
class Context:

    alignment: float = 0

    phase: str = "Unknown"

    session_score: float = 0

    probability: float = 0

    quality: float = 0

    pattern_winrate: float = 0


@dataclass
class Decision:

    recommendation: str

    bullish: bool

    confidence: float

    bull_score: float

    bear_score: float

    context_score: float

    setup_score: float

    quality: str


@dataclass
class Trade:

    entry: float

    stop: float

    tp1: float

    tp2: float

    tp3: float

    rr: float


@dataclass
class MarketData:

    price: float

    trend: str

    structure: str

    bos: str

    choch: str

    liquidity: str

    sweep: str

    order_block: str

    breaker: str

    mitigation: str

    fvg: str

    premium: dict

    volume: str

    displacement: str

    atr: dict

    ema50: float

    ema200: float

    rsi: float

    macd: str
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Signal:

    id: str

    symbol: str

    timeframe: str

    timestamp: datetime

    price: float

    trend: str

    structure: str

    bos: bool

    choch: bool

    liquidity: bool

    sweep: bool

    order_block: bool

    breaker: bool

    mitigation: bool

    fvg: bool

    premium: bool

    volume: bool

    displacement: bool

    rsi: float

    macd: bool

    bull_score: int

    bear_score: int

    confidence: float

    setup: str

    recommendation: str

    entry: float

    stop: float

    tp1: float

    tp2: float

    tp3: float

    rr: float

    result: Optional[str] = None

    closed_at: Optional[datetime] = None

    max_profit: float = 0

    max_drawdown: float = 0

    holding_minutes: int = 0
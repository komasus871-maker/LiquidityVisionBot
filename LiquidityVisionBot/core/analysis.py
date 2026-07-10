from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class Analysis:

    symbol: str = ""

    timeframe: str = ""

    timestamp: str = ""

    market: Dict[str, Any] = field(default_factory=dict)

    smart_money: Dict[str, Any] = field(default_factory=dict)

    indicators: Dict[str, Any] = field(default_factory=dict)

    context: Dict[str, Any] = field(default_factory=dict)

    scores: Dict[str, Any] = field(default_factory=dict)

    trade: Dict[str, Any] = field(default_factory=dict)

    statistics: Dict[str, Any] = field(default_factory=dict)

    metadata: Dict[str, Any] = field(default_factory=dict)
from dataclasses import dataclass, field


@dataclass
class EngineContext:

    df: object

    market: object = None

    signals: list = field(default_factory=list)

    score: object = None

    decision: object = None

    trade: object = None

    report: dict = field(default_factory=dict)
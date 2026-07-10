from dataclasses import dataclass


@dataclass
class Signal:

    name: str

    direction: str

    confidence: float

    strength: float

    weight: float

    active: bool

    reason: str
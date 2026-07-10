from dataclasses import dataclass


@dataclass
class Zone:

    kind: str

    high: float

    low: float

    strength: float

    active: bool
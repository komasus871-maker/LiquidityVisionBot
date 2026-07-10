from dataclasses import dataclass


@dataclass
class Pattern:

    samples: int

    winrate: float

    average_rr: float
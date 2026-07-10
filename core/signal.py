from dataclasses import dataclass, field
from typing import Any


@dataclass
class Signal:

    name: str

    direction: str

    strength: float

    confidence: float

    active: bool

    weight: int

    reason: str

    data: dict[str, Any] = field(default_factory=dict)
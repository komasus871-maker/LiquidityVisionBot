from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ValidationDecision:
    mode: str
    live_allowed: bool
    risk_multiplier: float
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        return data


class ValidationGate:
    """Prevent unvalidated analytics from being presented as live-ready."""

    def __init__(
        self,
        minimum_trades: int = 100,
        minimum_profit_factor: float = 1.15,
        minimum_expectancy_r: float = 0.05,
        maximum_drawdown_r: float = 12.0,
    ) -> None:
        self.minimum_trades = minimum_trades
        self.minimum_profit_factor = minimum_profit_factor
        self.minimum_expectancy_r = minimum_expectancy_r
        self.maximum_drawdown_r = maximum_drawdown_r

    def evaluate(self, metrics: Mapping[str, Any] | None) -> ValidationDecision:
        metrics = metrics or {}
        reasons: list[str] = []
        trades = int(metrics.get("trades") or 0)
        profit_factor = float(metrics.get("profit_factor") or 0.0)
        expectancy = float(metrics.get("expectancy_r") or 0.0)
        drawdown = float(metrics.get("max_drawdown_r") or 0.0)

        if trades < self.minimum_trades:
            reasons.append(f"Only {trades}/{self.minimum_trades} validated trades")
        if profit_factor < self.minimum_profit_factor:
            reasons.append(f"Profit factor {profit_factor:.2f} below {self.minimum_profit_factor:.2f}")
        if expectancy < self.minimum_expectancy_r:
            reasons.append(f"Expectancy {expectancy:.3f}R below {self.minimum_expectancy_r:.3f}R")
        if drawdown > self.maximum_drawdown_r:
            reasons.append(f"Drawdown {drawdown:.2f}R above {self.maximum_drawdown_r:.2f}R")

        if reasons:
            return ValidationDecision("PAPER", False, 0.0, tuple(reasons))
        return ValidationDecision("LIVE_VALIDATED", True, 1.0, ("Validation thresholds passed",))

from __future__ import annotations

from services.execution_models import PositionSize


class PositionSizer:
    """Risk-based sizing independent of leverage and exchange implementation."""

    @staticmethod
    def calculate(*, balance: float, risk_pct: float, entry: float, stop: float, max_notional_multiple: float = 3.0) -> PositionSize:
        balance = float(balance)
        risk_pct = float(risk_pct)
        entry = float(entry)
        stop = float(stop)
        if balance <= 0 or entry <= 0 or stop <= 0:
            raise ValueError("Balance, entry and stop must be positive")
        stop_distance = abs(entry - stop)
        if stop_distance <= 0:
            raise ValueError("Stop must differ from entry")
        if not 0 < risk_pct <= 5:
            raise ValueError("Risk must be between 0 and 5 percent")
        risk_amount = balance * risk_pct / 100.0
        quantity = risk_amount / stop_distance
        notional = quantity * entry
        max_notional = balance * max(0.1, float(max_notional_multiple))
        if notional > max_notional:
            notional = max_notional
            quantity = notional / entry
            risk_amount = quantity * stop_distance
        return PositionSize(
            quantity=quantity,
            notional=notional,
            risk_amount=risk_amount,
            stop_distance_pct=stop_distance / entry * 100.0,
        )

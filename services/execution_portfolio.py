from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable, Mapping

from services.execution_repositories import ExecutionRepository


@dataclass(frozen=True)
class PortfolioSnapshot:
    telegram_id: int
    open_positions: int
    gross_notional: float
    net_notional: float
    unrealized_pnl: float
    realized_pnl: float
    total_commission: float
    long_positions: int
    short_positions: int
    symbols: tuple[str, ...]
    equity_delta: float
    net_pnl_after_fees: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionPortfolioEngine:
    def __init__(self, repository: ExecutionRepository | None = None) -> None:
        self.repository = repository or ExecutionRepository()

    def snapshot(self, telegram_id: int) -> PortfolioSnapshot:
        return self.from_positions(telegram_id, self.repository.open_positions(telegram_id))

    @staticmethod
    def from_positions(telegram_id: int, positions: Iterable[Mapping[str, Any]]) -> PortfolioSnapshot:
        rows = list(positions)
        gross = net = unrealized = realized = commission = 0.0
        longs = shorts = 0
        symbols: list[str] = []
        for row in rows:
            qty = float(row.get("quantity") or 0.0)
            price = float(row.get("last_price") or row.get("average_entry") or 0.0)
            notional = qty * price
            side = str(row.get("side") or "").upper()
            direction = 1.0 if side == "LONG" else -1.0
            gross += abs(notional)
            net += direction * notional
            unrealized += float(row.get("unrealized_pnl") or 0.0)
            realized += float(row.get("realized_pnl") or 0.0)
            commission += float(row.get("total_commission") or 0.0)
            longs += int(side == "LONG")
            shorts += int(side == "SHORT")
            symbol = str(row.get("symbol") or "")
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        return PortfolioSnapshot(
            telegram_id=int(telegram_id), open_positions=len(rows), gross_notional=round(gross, 8),
            net_notional=round(net, 8), unrealized_pnl=round(unrealized, 8),
            realized_pnl=round(realized, 8), total_commission=round(commission, 8),
            long_positions=longs, short_positions=shorts, symbols=tuple(symbols),
            equity_delta=round(realized + unrealized - commission, 8),
            net_pnl_after_fees=round(realized + unrealized - commission, 8),
        )

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import isclose
from typing import Any

from database.database import connect


OPEN_STATUSES = ("OPEN", "PARTIALLY_FILLED", "PARTIALLY_CLOSED")
TERMINAL_EVENT_TYPES = ("CLOSED",)


@dataclass(frozen=True)
class PortfolioSnapshot:
    telegram_id: int
    starting_balance: float
    open_positions: int
    symbols: tuple[str, ...]
    gross_notional: float
    net_notional: float
    realized_gross_pnl: float
    unrealized_pnl: float
    commissions: float
    net_realized_pnl: float
    net_equity: float
    daily_realized_result: float
    realized_r: float
    confirmed_heat_r: float
    risk_complete: int
    risk_partial: int
    risk_missing: int
    risk_invalid: int
    resolved: bool
    rejection_count: int
    cooldown_symbols: tuple[str, ...]
    authority: str = "UNIFIED_POSITIONS+PORTFOLIO_LEDGER"

    @property
    def unresolved_risk_count(self) -> int:
        return self.risk_missing + self.risk_invalid

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["unresolved_risk_count"] = self.unresolved_risk_count
        return data


class ExecutionPortfolioEngine:
    """The single normalized read model for unified paper portfolio accounting.

    Position rows authorize lifetime/open-state totals.  The append-only ledger
    authorizes time-windowed realized results.  This split makes old upgraded
    positions visible without fabricating historical ledger timestamps.
    """

    def __init__(self, repository: Any | None = None) -> None:
        self.repository = repository

    def snapshot(self, telegram_id: int, *, cooldown_min: int = 30,
                 now: datetime | None = None) -> PortfolioSnapshot:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        day_start = current.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        cooldown_start = current - timedelta(minutes=max(0, int(cooldown_min)))
        with connect() as conn:
            profile = conn.execute(
                "SELECT paper_balance FROM copy_profiles WHERE telegram_id=?", (telegram_id,)
            ).fetchone()
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM paper_execution_positions WHERE telegram_id=? ORDER BY id", (telegram_id,)
            ).fetchall()]
            daily = conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN entry_type='REALIZED_PNL' THEN amount
                                             WHEN entry_type='COMMISSION' THEN -amount ELSE 0 END),0)
                   FROM paper_portfolio_ledger WHERE telegram_id=? AND occurred_at>=?""",
                (telegram_id, day_start.isoformat()),
            ).fetchone()
            rejected = conn.execute(
                "SELECT COUNT(*) FROM copy_execution_journal WHERE telegram_id=? AND status='REJECTED'",
                (telegram_id,),
            ).fetchone()
            cooldown_rows = conn.execute(
                """SELECT DISTINCT UPPER(TRIM(p.symbol)) symbol
                   FROM paper_position_lifecycle_events e
                   JOIN paper_execution_positions p ON p.id=e.position_id
                   WHERE e.telegram_id=? AND e.event_type='CLOSED' AND e.created_at>=?""",
                (telegram_id, cooldown_start.isoformat()),
            ).fetchall()

        starting = float(profile[0] if profile else 10000.0)
        realized = sum(float(row.get("realized_pnl") or 0) for row in rows)
        commissions = sum(float(row.get("total_commission") or 0) for row in rows)
        realized_r = sum(float(row.get("realized_r") or 0) for row in rows)
        open_rows = [row for row in rows if str(row.get("status")) in OPEN_STATUSES and float(row.get("quantity") or 0) > 0]
        gross = net = unrealized = heat = 0.0
        complete = partial = missing = invalid = 0
        symbols: set[str] = set()
        for row in open_rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol:
                symbols.add(symbol)
            qty = float(row.get("quantity") or 0)
            initial_qty = float(row.get("initial_quantity") or 0)
            entry = float(row.get("average_entry") or 0)
            price = float(row.get("last_price") or entry)
            side = str(row.get("side") or "").upper()
            direction = 1.0 if side == "LONG" else -1.0
            notional = qty * price
            gross += abs(notional)
            net += direction * notional
            unrealized += float(row.get("unrealized_pnl") or 0)
            stop = row.get("stop_loss")
            risk = row.get("initial_risk_amount")
            if stop is None or risk is None or initial_qty <= 0:
                missing += 1
                continue
            stop_value = float(stop)
            risk_value = float(risk)
            expected = abs(entry - stop_value) * initial_qty
            if entry <= 0 or stop_value <= 0 or risk_value <= 0 or expected <= 0 or side not in {"LONG", "SHORT"}:
                invalid += 1
                continue
            tolerance = max(1e-8, expected * 0.02)
            if abs(expected - risk_value) > tolerance or qty > initial_qty + 1e-10:
                invalid += 1
                continue
            remaining = qty / initial_qty
            heat += remaining
            if remaining < 1.0 - 1e-10:
                partial += 1
            else:
                complete += 1

        net_realized = realized - commissions
        return PortfolioSnapshot(
            telegram_id=int(telegram_id), starting_balance=round(starting, 8),
            open_positions=len(open_rows), symbols=tuple(sorted(symbols)),
            gross_notional=round(gross, 8), net_notional=round(net, 8),
            realized_gross_pnl=round(realized, 8), unrealized_pnl=round(unrealized, 8),
            commissions=round(commissions, 8), net_realized_pnl=round(net_realized, 8),
            net_equity=round(starting + net_realized + unrealized, 8),
            daily_realized_result=round(float(daily[0] or 0), 8), realized_r=round(realized_r, 8),
            confirmed_heat_r=round(heat, 8), risk_complete=complete, risk_partial=partial,
            risk_missing=missing, risk_invalid=invalid, resolved=(missing + invalid == 0),
            rejection_count=int(rejected[0] or 0),
            cooldown_symbols=tuple(sorted(str(row[0]) for row in cooldown_rows if row[0])),
        )

    def parity_report(self, telegram_id: int, *, cooldown_min: int = 30) -> dict[str, Any]:
        """Structured shadow comparison; never mutates either accounting source."""
        unified = self.snapshot(telegram_id, cooldown_min=cooldown_min)
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        cooldown_start = (datetime.now(timezone.utc) - timedelta(minutes=max(0, cooldown_min))).isoformat()
        with connect() as conn:
            legacy = conn.execute(
                """SELECT
                    COALESCE(SUM(CASE WHEN status IN ('OPEN','PARTIAL') THEN 1 ELSE 0 END),0) AS legacy_open_count,
                    COALESCE(SUM(CASE WHEN status IN ('OPEN','PARTIAL') THEN initial_risk_r*remaining_fraction ELSE 0 END),0) AS legacy_heat_r,
                    COALESCE(SUM(realized_pnl),0) AS legacy_realized_pnl,
                    COALESCE(SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END),0) AS legacy_rejection_count
                    FROM paper_positions WHERE telegram_id=?""", (telegram_id,)
            ).fetchone()
            legacy_daily = conn.execute(
                """SELECT COALESCE(SUM(realized_pnl_delta),0) FROM execution_events
                   WHERE telegram_id=? AND created_at>=? AND event_type IN ('PARTIAL_FILLED','CLOSED')""",
                (telegram_id, day_start),
            ).fetchone()
            legacy_symbols = conn.execute(
                """SELECT DISTINCT UPPER(TRIM(symbol)) FROM paper_positions
                   WHERE telegram_id=? AND status IN ('OPEN','PARTIAL')""", (telegram_id,)
            ).fetchall()
            legacy_cooldown = conn.execute(
                """SELECT DISTINCT UPPER(TRIM(symbol)) FROM paper_positions
                   WHERE telegram_id=? AND status='CLOSED' AND closed_at>=?""",
                (telegram_id, cooldown_start),
            ).fetchall()
        legacy_open, legacy_heat, legacy_realized, legacy_rejected = (
            legacy[0], legacy[1], legacy[2], legacy[3]
        )
        legacy_equity = unified.starting_balance + float(legacy_realized or 0)
        legacy_symbol_set = {str(row[0]) for row in legacy_symbols if row[0]}
        legacy_cooldown_set = {str(row[0]) for row in legacy_cooldown if row[0]}
        fields = {
            "open_count": (int(legacy_open or 0), unified.open_positions),
            "heat_r": (float(legacy_heat or 0), unified.confirmed_heat_r),
            "realized_pnl": (float(legacy_realized or 0), unified.realized_gross_pnl),
            "daily_pnl": (float(legacy_daily[0] or 0), unified.daily_realized_result),
            "equity": (legacy_equity, unified.net_equity),
            "symbols": (tuple(sorted(legacy_symbol_set)), unified.symbols),
            "cooldown": (tuple(sorted(legacy_cooldown_set)), unified.cooldown_symbols),
            "rejections": (int(legacy_rejected or 0), unified.rejection_count),
        }
        def differs(values: tuple[Any, Any]) -> bool:
            left, right = values
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                return not isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-8)
            return left != right

        mismatches = tuple(name for name, values in fields.items() if differs(values))
        dangerous = ("risk" if not unified.resolved else None)
        return {
            "status": "UNRESOLVED" if dangerous else "MISMATCH" if mismatches else "MATCH",
            "mismatches": mismatches, "dangerous_reason": dangerous,
            "expected_historical_difference": bool(mismatches and unified.open_positions == 0),
            "legacy": {name: values[0] for name, values in fields.items()},
            "unified": {name: values[1] for name, values in fields.items()},
        }

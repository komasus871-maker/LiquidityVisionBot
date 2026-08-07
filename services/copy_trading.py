from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import connect
from services.execution_models import PortfolioState, PositionSizingMode, RiskProfile
from services.execution_validator import ExecutionValidator
from services.copy_execution_planner import CopyExecutionPlanner
from services.copy_execution_journal import CopyExecutionJournal, JournalStatus
from services.paper_execution_lifecycle import PaperExecutionLifecycle
from services.execution_queue import ExecutionQueueService
from services.copy_training import CopyTrainingService
from services.copy_similarity import CopySimilarityService
from services.portfolio_reconciliation import PortfolioReconciliationService
from services.execution_repositories import ExecutionRepository, UnifiedOpenPositionState
from services.execution_portfolio import ExecutionPortfolioEngine
from services.copy_controls import normalize_copy_symbol


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _day_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


class CopyTradingService:
    """Idempotent, multi-user paper execution service with a production-grade risk ledger."""

    TERMINAL_SIGNAL_STATUSES = {"TP3", "STOP", "BREAKEVEN", "INVALIDATED", "EXPIRED", "CLOSED"}
    OPEN_SIGNAL_STATUSES = {"ACTIVE", "TP1", "TP2"}
    PROFILE_TEMPLATES: dict[str, dict[str, Any]] = {
        "CONSERVATIVE": {
            "risk_pct": 0.25, "sizing_mode": "RISK_PERCENT", "leverage": 1,
            "max_positions": 2, "max_heat_r": 1.5, "daily_loss_pct": 1.0,
            "max_slippage_pct": 0.15, "min_confidence": 65.0,
            "max_notional_pct": 20.0, "max_portfolio_exposure_pct": 40.0,
            "symbol_cooldown_min": 60,
        },
        "STANDARD": {
            "risk_pct": 0.5, "sizing_mode": "RISK_PERCENT", "leverage": 2,
            "max_positions": 3, "max_heat_r": 2.5, "daily_loss_pct": 2.0,
            "max_slippage_pct": 0.25, "min_confidence": 55.0,
            "max_notional_pct": 35.0, "max_portfolio_exposure_pct": 70.0,
            "symbol_cooldown_min": 30,
        },
        "AGGRESSIVE": {
            "risk_pct": 1.0, "sizing_mode": "RISK_PERCENT", "leverage": 3,
            "max_positions": 5, "max_heat_r": 4.0, "daily_loss_pct": 4.0,
            "max_slippage_pct": 0.4, "min_confidence": 50.0,
            "max_notional_pct": 50.0, "max_portfolio_exposure_pct": 100.0,
            "symbol_cooldown_min": 15,
        },
    }
    CUSTOM_FIELDS = {
        "risk_pct", "sizing_mode", "fixed_usdt", "equity_pct", "copy_multiplier",
        "leverage", "max_positions", "max_heat_r", "daily_loss_pct", "max_slippage_pct",
        "min_confidence", "max_notional_pct", "max_portfolio_exposure_pct",
        "symbol_cooldown_min", "symbol_policy", "symbol_whitelist_json",
        "symbol_blacklist_json", "timeframe_filters_json", "setup_filters_json",
        "direction_filters_json", "allow_experimental",
    }

    def __init__(self) -> None:
        self.validator = ExecutionValidator()
        self.planner = CopyExecutionPlanner(self.validator)
        self.execution_journal = CopyExecutionJournal()
        self.paper_lifecycle = PaperExecutionLifecycle()
        self.execution_queue = ExecutionQueueService(journal=self.execution_journal)
        self.training = CopyTrainingService()
        self.similarity = CopySimilarityService()
        self.reconciliation = PortfolioReconciliationService()
        self.execution_repository = ExecutionRepository()
        self.portfolio_engine = ExecutionPortfolioEngine()

    def ensure_profile(self, telegram_id: int) -> dict[str, Any]:
        now = _now()
        with connect() as conn:
            conn.execute(
                """INSERT INTO copy_profiles(
                       telegram_id,enabled,mode,profile_name,risk_pct,sizing_mode,fixed_usdt,equity_pct,
                       copy_multiplier,leverage,auto_copy,max_positions,max_heat_r,daily_loss_pct,
                       max_slippage_pct,paper_balance,min_confidence,max_notional_pct,symbol_cooldown_min,
                       max_portfolio_exposure_pct,symbol_policy,symbol_whitelist_json,symbol_blacklist_json,
                       timeframe_filters_json,setup_filters_json,direction_filters_json,allow_experimental,
                       created_at,updated_at
                   ) VALUES(?,0,'PAPER','STANDARD',0.5,'RISK_PERCENT',0,10,1,2,0,3,2.5,2.0,
                            0.25,10000,55,35,30,70,'ALL','[]','[]','[]','[]','[]',0,?,?)
                   ON CONFLICT(telegram_id) DO NOTHING""",
                (telegram_id, now, now),
            )
            row = conn.execute("SELECT * FROM copy_profiles WHERE telegram_id=?", (telegram_id,)).fetchone()
        return dict(row)

    def update_profile(self, telegram_id: int, *, actor: str = "USER",
                       mark_custom: bool = True, **fields: Any) -> dict[str, Any]:
        allowed = {
            "enabled", "profile_name", "risk_pct", "sizing_mode", "fixed_usdt", "equity_pct",
            "copy_multiplier", "leverage", "auto_copy", "max_positions", "max_heat_r", "daily_loss_pct",
            "max_slippage_pct", "paper_balance", "min_confidence", "max_notional_pct",
            "symbol_cooldown_min", "max_portfolio_exposure_pct", "symbol_policy",
            "symbol_whitelist_json", "symbol_blacklist_json", "timeframe_filters_json",
            "setup_filters_json", "direction_filters_json", "allow_experimental",
        }
        fields = {key: value for key, value in fields.items() if key in allowed}
        # These flags represent one activation intent. Preserve an explicit
        # two-field override, but prevent a single-field update from leaving a
        # profile silently half-armed.
        if "enabled" in fields and "auto_copy" not in fields:
            fields["auto_copy"] = int(bool(fields["enabled"]))
        elif "auto_copy" in fields and "enabled" not in fields:
            fields["enabled"] = int(bool(fields["auto_copy"]))
        self.ensure_profile(telegram_id)
        current = self.ensure_profile(telegram_id)
        if mark_custom and set(fields).intersection(self.CUSTOM_FIELDS):
            fields["profile_name"] = "CUSTOM"
        candidate = {**current, **fields}
        normalized = self._validate_profile(candidate)
        fields = {key: normalized[key] for key in fields}
        if fields:
            before = {key: current.get(key) for key in sorted(fields) if key != "updated_at"}
            after = {key: fields.get(key) for key in sorted(fields) if key != "updated_at"}
            fields["updated_at"] = _now()
            assignments = ",".join(f"{key}=?" for key in fields)
            with connect() as conn:
                conn.execute(f"UPDATE copy_profiles SET {assignments} WHERE telegram_id=?", (*fields.values(), telegram_id))
                conn.execute("""INSERT INTO copy_profile_events(telegram_id,event_type,actor,before_json,
                    after_json,changed_fields_json,created_at) VALUES(?,?,?,?,?,?,?)""", (
                    telegram_id, "PROFILE_UPDATED", str(actor)[:40],
                    json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True),
                    json.dumps(sorted(before), sort_keys=True), fields["updated_at"]))
        return self.ensure_profile(telegram_id)

    def select_profile(self, telegram_id: int, name: str, *, actor: str = "USER") -> dict[str, Any]:
        normalized = str(name or "").strip().upper()
        if normalized == "CUSTOM":
            return self.update_profile(telegram_id, actor=actor, mark_custom=False,
                                       profile_name="CUSTOM")
        template = self.PROFILE_TEMPLATES.get(normalized)
        if template is None:
            raise ValueError("Unknown profile; use CONSERVATIVE, STANDARD, AGGRESSIVE, or CUSTOM")
        return self.update_profile(telegram_id, actor=actor, mark_custom=False,
                                   profile_name=normalized, **template)


    @staticmethod
    def _validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(profile)
        try:
            mode = PositionSizingMode(str(normalized.get("sizing_mode") or PositionSizingMode.RISK_PERCENT.value).upper())
            risk_pct = float(normalized.get("risk_pct") or 0)
            fixed_raw = normalized.get("fixed_usdt")
            leverage_raw = normalized.get("leverage")
            positions_raw = normalized.get("max_positions")
            fixed_usdt = float(0 if fixed_raw is None else fixed_raw)
            equity_pct = float(normalized.get("equity_pct") or 10)
            copy_multiplier = float(normalized.get("copy_multiplier") or 1)
            leverage = int(1 if leverage_raw is None else leverage_raw)
            max_positions = int(0 if positions_raw is None else positions_raw)
            max_heat = float(normalized.get("max_heat_r") or 2.5)
            daily_loss = float(normalized.get("daily_loss_pct") or 2.0)
            max_slippage = float(normalized.get("max_slippage_pct") or 0.25)
            paper_balance = float(normalized.get("paper_balance") or 10_000)
            min_confidence = float(normalized.get("min_confidence") or 55)
            max_notional = float(normalized.get("max_notional_pct") or 35)
            max_exposure = float(normalized.get("max_portfolio_exposure_pct") or 70)
            cooldown = int(normalized.get("symbol_cooldown_min") or 30)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid copy profile value: {exc}") from exc
        if not 0.05 <= risk_pct <= 5.0:
            raise ValueError("risk_pct must be between 0.05 and 5")
        if mode is PositionSizingMode.FIXED_USDT and not 5 <= fixed_usdt <= 10_000_000:
            raise ValueError("fixed_usdt must be between 5 and 10000000 in FIXED_USDT mode")
        if not 0.1 <= equity_pct <= 100:
            raise ValueError("equity_pct must be between 0.1 and 100")
        if not 0.01 <= copy_multiplier <= 10:
            raise ValueError("copy_multiplier must be between 0.01 and 10")
        if not 1 <= leverage <= 125:
            raise ValueError("leverage must be between 1 and 125")
        if not 1 <= max_positions <= 20:
            raise ValueError("max_positions must be between 1 and 20")
        if not 0.25 <= max_heat <= 20 or not 0.1 <= daily_loss <= 25:
            raise ValueError("heat or daily loss limit is outside the safe range")
        if not 0 <= max_slippage <= 5 or not 100 <= paper_balance <= 100_000_000:
            raise ValueError("slippage or paper balance is outside the safe range")
        if not 0 <= min_confidence <= 100 or not 1 <= max_notional <= 100:
            raise ValueError("confidence or maximum notional is outside the safe range")
        if not 1 <= max_exposure <= 500 or not 0 <= cooldown <= 1440:
            raise ValueError("portfolio exposure or cooldown is outside the safe range")
        profile_name = str(normalized.get("profile_name") or "CUSTOM").upper()
        if profile_name not in {*CopyTradingService.PROFILE_TEMPLATES, "CUSTOM"}:
            raise ValueError("profile_name is invalid")
        symbol_policy = str(normalized.get("symbol_policy") or "ALL").upper()
        if symbol_policy not in {"ALL", "WHITELIST"}:
            raise ValueError("symbol_policy must be ALL or WHITELIST")
        list_fields = {
            "symbol_whitelist_json": CopyTradingService.normalize_symbol,
            "symbol_blacklist_json": CopyTradingService.normalize_symbol,
            "timeframe_filters_json": lambda value: str(value).strip().lower(),
            "setup_filters_json": lambda value: " ".join(str(value).strip().lower().split()),
            "direction_filters_json": lambda value: str(value).strip().upper(),
        }
        for field, normalizer in list_fields.items():
            raw = normalized.get(field) or "[]"
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(f"{field} must be a JSON list") from exc
            if not isinstance(raw, (list, tuple, set)):
                raise ValueError(f"{field} must be a list")
            values = sorted({normalizer(value) for value in raw if normalizer(value)})
            if len(values) > 100:
                raise ValueError(f"{field} exceeds 100 values")
            normalized[field] = json.dumps(values, separators=(",", ":"))
        normalized.update(
            sizing_mode=mode.value, fixed_usdt=fixed_usdt, leverage=leverage,
            equity_pct=equity_pct, copy_multiplier=copy_multiplier,
            auto_copy=int(bool(normalized.get("auto_copy"))), risk_pct=risk_pct,
            max_positions=max_positions, max_heat_r=max_heat, daily_loss_pct=daily_loss,
            max_slippage_pct=max_slippage, paper_balance=paper_balance,
            min_confidence=min_confidence, max_notional_pct=max_notional,
            max_portfolio_exposure_pct=max_exposure, symbol_cooldown_min=cooldown,
            profile_name=profile_name, symbol_policy=symbol_policy,
            allow_experimental=int(bool(normalized.get("allow_experimental"))),
        )
        return normalized

    @staticmethod
    def normalize_symbol(value: Any) -> str:
        return normalize_copy_symbol(value)

    @staticmethod
    def _json_tuple(value: Any) -> tuple[str, ...]:
        try:
            parsed = json.loads(value or "[]") if isinstance(value, str) else value
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = []
        return tuple(str(item) for item in parsed) if isinstance(parsed, (list, tuple)) else ()

    def panic(self, telegram_id: int) -> int:
        now = _now()
        closed_unified = 0
        for position in self.execution_repository.open_positions(telegram_id):
            price = float(position.get("last_price") or position.get("average_entry") or 0.0)
            if price <= 0:
                continue
            result = self.paper_lifecycle.apply_signal_transition(
                int(position["id"]),
                signal_status="PANIC",
                price=price,
                event_key=f"panic:{position['id']}",
                reason="PANIC_CLOSE",
                commission_rate=self.paper_lifecycle.DEFAULT_COMMISSION_RATE,
            )
            if result.applied:
                closed_unified += 1
                self._project_unified_position(result.position, event=result)
        with connect() as conn:
            conn.execute("UPDATE copy_profiles SET enabled=0,auto_copy=0,updated_at=? WHERE telegram_id=?", (now, telegram_id))
            rows = conn.execute(
                "SELECT * FROM paper_positions WHERE telegram_id=? AND status IN ('OPEN','PARTIAL')",
                (telegram_id,),
            ).fetchall()
        legacy_only: list[dict[str, Any]] = []
        for row in rows:
            position = dict(row)
            unified = self.execution_repository.position_for_signal(
                telegram_id, int(position["signal_id"])
            )
            if unified:
                self._project_unified_position(unified)
                continue
            legacy_only.append(position)
        with connect() as conn:
            for position in legacy_only:
                exit_price = float(position.get("last_price") or position["entry_price"])
                self._close_position_conn(conn, position, exit_price, "PANIC_CLOSE", now)
        return closed_unified + len(legacy_only)

    def profile_stats(self, telegram_id: int) -> dict[str, Any]:
        reconciliation = self.reconciliation.reconcile(telegram_id)
        profile = self.ensure_profile(telegram_id)
        with connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) total,
                   SUM(CASE WHEN status IN ('OPEN','PARTIAL') THEN 1 ELSE 0 END) open_count,
                   SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) closed_count,
                   SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) rejected_count,
                   COALESCE(SUM(CASE WHEN status='CLOSED' THEN realized_r ELSE 0 END),0) realized_r,
                   COALESCE(SUM(realized_pnl),0) realized_pnl,
                   COALESCE(AVG(CASE WHEN status='CLOSED' THEN realized_r END),0) avg_r,
                   COALESCE(SUM(CASE WHEN status='CLOSED' AND realized_r>0 THEN 1 ELSE 0 END),0) wins,
                   COALESCE(SUM(CASE WHEN status='CLOSED' AND realized_r<0 THEN 1 ELSE 0 END),0) losses
                   FROM paper_positions WHERE telegram_id=?""",
                (telegram_id,),
            ).fetchone()
            daily = conn.execute(
                """SELECT COALESCE(SUM(realized_pnl_delta),0) pnl
                   FROM execution_events
                   WHERE telegram_id=? AND created_at>=? AND event_type IN ('PARTIAL_FILLED','CLOSED')""",
                (telegram_id, _day_start()),
            ).fetchone()
        result = dict(row)
        accounting = self.portfolio_engine.snapshot(
            telegram_id, cooldown_min=int(profile.get("symbol_cooldown_min") or 30)
        )
        parity = self.portfolio_engine.parity_report(
            telegram_id, cooldown_min=int(profile.get("symbol_cooldown_min") or 30)
        )
        mode = self._accounting_mode()
        legacy_daily_pnl = float(daily[0] or 0.0)
        legacy_equity = float(profile["paper_balance"]) + float(result.get("realized_pnl") or 0.0)
        result["legacy_daily_pnl"] = legacy_daily_pnl
        result["daily_pnl"] = legacy_daily_pnl if mode == "LEGACY" else accounting.daily_realized_result
        result["equity"] = legacy_equity if mode == "LEGACY" else accounting.net_equity
        closed = int(result.get("closed_count") or 0)
        result["win_rate"] = (float(result.get("wins") or 0) / closed * 100.0) if closed else 0.0
        top_rejection = self.rejection_summary(telegram_id, limit=1)
        result["top_rejection_code"] = top_rejection[0]["code"] if top_rejection else None
        result["top_rejection_count"] = top_rejection[0]["count"] if top_rejection else 0
        result.update({f"reconciliation_{k}": v for k, v in reconciliation.as_dict().items() if k != "telegram_id"})
        unified, legacy_signal_ids, hybrid_count = self._position_identity_state(telegram_id)
        unified_heat_by_signal = dict(unified.heat_r_by_signal)
        duplicate_unified_heat = sum(
            unified_heat_by_signal.get(signal_id, 0.0) for signal_id in legacy_signal_ids
        )
        unified_confirmed_heat = max(0.0, unified.confirmed_heat_r - duplicate_unified_heat)
        unresolved_unified_signals = unified.unresolved_risk_signal_ids.difference(legacy_signal_ids)
        unresolved_unified_count = len(unresolved_unified_signals)
        portfolio_resolved = reconciliation.portfolio_state_resolved and unresolved_unified_count == 0
        result.update(
            legacy_confirmed_open=reconciliation.confirmed_active_legacy_count,
            unified_open_positions=unified.open_count,
            hybrid_open_positions=hybrid_count,
            position_state_source="HYBRID_LEGACY_UNIFIED",
            unified_symbols=unified.symbols,
            unified_confirmed_heat_r=unified_confirmed_heat,
            hybrid_confirmed_heat_r=(
                reconciliation.confirmed_active_heat_r + unified_confirmed_heat
            ),
            hybrid_portfolio_state_resolved=portfolio_resolved,
            accounting_authority=("LEGACY_ROLLBACK" if mode == "LEGACY" else accounting.authority),
            accounting_source_mode=mode,
            unified_realized_pnl=accounting.realized_gross_pnl,
            unified_realized_r=accounting.realized_r,
            unified_commission=accounting.commissions,
            unified_net_realized_pnl=accounting.net_realized_pnl,
            unified_unrealized_pnl=accounting.unrealized_pnl,
            unified_equity=accounting.net_equity,
            unified_daily_pnl=accounting.daily_realized_result,
            unified_gross_notional=accounting.gross_notional,
            unified_net_notional=accounting.net_notional,
            unified_risk_complete=accounting.risk_complete,
            unified_risk_partial=accounting.risk_partial,
            unified_risk_missing=accounting.risk_missing,
            unified_risk_invalid=accounting.risk_invalid,
            unified_unresolved_risk_positions=accounting.unresolved_risk_count,
            cooldown_source="UNIFIED_TERMINAL_LIFECYCLE",
            parity_status=parity["status"],
            parity_mismatches=parity["mismatches"],
            parity_expected_historical_difference=parity["expected_historical_difference"],
        )
        result.update(self.performance_stats(telegram_id))
        return result

    def performance_stats(self, telegram_id: int) -> dict[str, Any]:
        """Separate policy, strategy and actual execution results without outcome relabeling."""
        with connect() as conn:
            journal = conn.execute("""SELECT COUNT(*) attempts,
                SUM(CASE WHEN status='EXECUTED' THEN 1 ELSE 0 END) accepted,
                SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) rejected
                FROM copy_execution_journal WHERE telegram_id=?""", (telegram_id,)).fetchone()
            positions = [dict(row) for row in conn.execute("""SELECT id,status,realized_pnl,realized_r,
                total_commission,close_reason,closed_at FROM paper_execution_positions
                WHERE telegram_id=? ORDER BY COALESCE(closed_at,created_at),id""",
                (telegram_id,)).fetchall()]
            fills = conn.execute("""SELECT COUNT(*) fills,COALESCE(SUM(commission),0) fees,
                COALESCE(AVG(slippage_pct),0) avg_slippage FROM paper_execution_fills
                WHERE telegram_id=?""", (telegram_id,)).fetchone()
        closed = [row for row in positions if row["status"] == "CLOSED"]
        interventions = [row for row in closed if any(token in str(row.get("close_reason") or "").upper()
                         for token in ("MANUAL", "PANIC"))]
        pure = [row for row in closed if row not in interventions]
        r_values = [float(row.get("realized_r") or 0.0) for row in pure]
        wins = [value for value in r_values if value > 1e-12]
        losses = [value for value in r_values if value < -1e-12]
        equity = peak = max_drawdown = 0.0
        for value in r_values:
            equity += value
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, equity - peak)
        gross_pnl = sum(float(row.get("realized_pnl") or 0.0) for row in closed)
        total_fees = sum(float(row.get("total_commission") or 0.0) for row in positions)
        return {
            "policy_eligible": int(journal["attempts"] or 0),
            "policy_accepted": int(journal["accepted"] or 0),
            "policy_rejected": int(journal["rejected"] or 0),
            "execution_opened": len(positions), "execution_closed": len(closed),
            "strategy_closed": len(pure), "strategy_wins": len(wins),
            "strategy_losses": len(losses), "strategy_breakeven": len(pure) - len(wins) - len(losses),
            "strategy_win_rate": len(wins) / len(pure) * 100 if pure else 0.0,
            "strategy_expectancy_r": sum(r_values) / len(r_values) if r_values else None,
            "strategy_average_win_r": sum(wins) / len(wins) if wins else None,
            "strategy_average_loss_r": sum(losses) / len(losses) if losses else None,
            "strategy_profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
            "strategy_drawdown_proxy_r": max_drawdown if r_values else None,
            "actual_gross_pnl": gross_pnl, "actual_fees": total_fees,
            "actual_net_pnl": gross_pnl - total_fees,
            "actual_fill_count": int(fills["fills"] or 0),
            "actual_average_slippage_pct": float(fills["avg_slippage"] or 0.0),
            "manual_intervention_count": len(interventions),
            "manual_intervention_realized_r": sum(float(row.get("realized_r") or 0.0)
                                                    for row in interventions),
            "manual_counterfactual_status": "NOT_RECONSTRUCTED",
        }

    @staticmethod
    def _accounting_mode() -> str:
        mode = os.getenv("PORTFOLIO_ACCOUNTING_SOURCE", "SHADOW").strip().upper()
        return mode if mode in {"LEGACY", "SHADOW", "UNIFIED"} else "SHADOW"

    def rejection_summary(self, telegram_id: int, limit: int = 5) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 20))
        with connect() as conn:
            rows = conn.execute(
                """SELECT rejection_code, COUNT(*) count
                   FROM paper_positions
                   WHERE telegram_id=? AND status='REJECTED'
                   GROUP BY rejection_code
                   ORDER BY count DESC""",
                (telegram_id,),
            ).fetchall()
        return [
            {"code": str(row[0] or "UNKNOWN"), "count": int(row[1] or 0)}
            for row in rows[:safe_limit]
        ]

    def sync_signal(self, signal: dict[str, Any], *, profiles: list[dict[str, Any]] | None = None) -> dict[str, int]:
        opened = updated = closed = rejected = skipped = 0
        status = str(signal.get("status") or "").upper()
        if profiles is None:
            with connect() as conn:
                profiles = [dict(row) for row in conn.execute("""SELECT p.* FROM copy_profiles p
                    WHERE p.mode='PAPER' AND (p.enabled=1 OR EXISTS(
                        SELECT 1 FROM paper_positions lp WHERE lp.telegram_id=p.telegram_id
                        AND lp.status IN ('OPEN','PARTIAL')) OR EXISTS(
                        SELECT 1 FROM paper_execution_positions up WHERE up.telegram_id=p.telegram_id
                        AND up.status IN ('OPEN','PARTIALLY_FILLED','PARTIALLY_CLOSED')))""").fetchall()]
        for profile in profiles:
            telegram_id = int(profile["telegram_id"])
            existing = self._get_position(telegram_id, int(signal["id"]))
            unified = self.execution_repository.position_for_signal(
                telegram_id, int(signal["id"])
            )
            if status in self.OPEN_SIGNAL_STATUSES and existing is None:
                owner = signal.get("owner_telegram_id")
                if owner not in {None, 0, "0"} and int(owner) != telegram_id and unified is None:
                    # User-owned analysis is private. Global/master signals use
                    # a null (or legacy zero) owner and may fan out by profile.
                    skipped += 1
                    continue
                if not bool(profile.get("enabled")) or not bool(profile.get("auto_copy")):
                    skipped += 1
                    continue
                if unified and str(unified.get("status")) not in {"CLOSED", "CANCELLED", "FAILED"}:
                    result = self._sync_unified_existing(unified, signal)
                else:
                    result = self._open(telegram_id, profile, signal)
                opened += int(result == "OPEN")
                rejected += int(result == "REJECTED")
                updated += int(result == "UPDATED")
            elif existing and existing["status"] in {"OPEN", "PARTIAL"}:
                outcome = (
                    self._sync_unified_existing(unified, signal)
                    if unified and str(unified.get("status")) not in {"CLOSED", "CANCELLED", "FAILED"}
                    else self._sync_existing(existing, signal)
                )
                updated += int(outcome == "UPDATED")
                closed += int(outcome == "CLOSED")
                skipped += int(outcome == "SKIPPED")
            elif existing and existing["status"] == "REJECTED":
                outcome = self._sync_rejected(existing, signal)
                updated += int(outcome == "UPDATED")
                skipped += int(outcome == "SKIPPED")
            elif unified and str(unified.get("status")) not in {"CLOSED", "CANCELLED", "FAILED"}:
                outcome = self._sync_unified_existing(unified, signal)
                updated += int(outcome == "UPDATED")
                closed += int(outcome == "CLOSED")
                skipped += int(outcome == "SKIPPED")
            else:
                skipped += 1
        return {"opened": opened, "updated": updated, "closed": closed, "rejected": rejected, "skipped": skipped}

    def sync_all(self) -> dict[str, int]:
        totals = {"opened": 0, "updated": 0, "closed": 0, "rejected": 0, "skipped": 0}
        with connect() as conn:
            recent = [dict(row) for row in conn.execute(
                """SELECT * FROM signals
                   WHERE status IN ('ACTIVE','TP1','TP2','TP3','STOP','BREAKEVEN','INVALIDATED','EXPIRED')
                   ORDER BY id DESC LIMIT 500"""
            ).fetchall()]
            # The bounded recent scan must never strand an older economically
            # open PAPER position. Include every signal currently referenced by
            # either lifecycle authority, then deduplicate by signal ID.
            position_signals = [dict(row) for row in conn.execute("""SELECT DISTINCT s.*
                FROM signals s LEFT JOIN paper_execution_positions up ON up.signal_id=s.id
                LEFT JOIN paper_positions lp ON lp.signal_id=s.id
                WHERE up.status IN ('OPEN','PARTIALLY_FILLED','PARTIALLY_CLOSED')
                   OR lp.status IN ('OPEN','PARTIAL')""").fetchall()]
            profiles = [dict(row) for row in conn.execute("""SELECT p.* FROM copy_profiles p
                WHERE p.mode='PAPER' AND (p.enabled=1 OR EXISTS(
                    SELECT 1 FROM paper_positions lp WHERE lp.telegram_id=p.telegram_id
                    AND lp.status IN ('OPEN','PARTIAL')) OR EXISTS(
                    SELECT 1 FROM paper_execution_positions up WHERE up.telegram_id=p.telegram_id
                        AND up.status IN ('OPEN','PARTIALLY_FILLED','PARTIALLY_CLOSED')))""").fetchall()]
        signals_by_id = {int(signal["id"]): signal for signal in recent}
        signals_by_id.update({int(signal["id"]): signal for signal in position_signals})
        signals = sorted(signals_by_id.values(), key=lambda item: int(item["id"]), reverse=True)
        for signal in signals:
            result = self.sync_signal(signal, profiles=profiles)
            for key in totals:
                totals[key] += result[key]
        return totals

    def recent_events(self, telegram_id: int, limit: int = 15) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM execution_events WHERE telegram_id=? ORDER BY id DESC LIMIT {safe_limit}",
                (telegram_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _get_position(self, telegram_id: int, signal_id: int) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM paper_positions WHERE telegram_id=? AND signal_id=?",
                (telegram_id, signal_id),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _risk_profile(profile: dict[str, Any]) -> RiskProfile:
        return RiskProfile(
            risk_pct=float(profile["risk_pct"]),
            sizing_mode=PositionSizingMode(str(profile.get("sizing_mode") or "RISK_PERCENT")),
            fixed_usdt=float(profile.get("fixed_usdt") or 0),
            equity_pct=float(profile.get("equity_pct") or 10),
            copy_multiplier=float(profile.get("copy_multiplier") or 1),
            leverage=int(profile.get("leverage") or 1),
            auto_copy=bool(profile.get("auto_copy")),
            max_positions=int(profile["max_positions"]),
            max_heat_r=float(profile["max_heat_r"]),
            daily_loss_pct=float(profile["daily_loss_pct"]),
            max_slippage_pct=float(profile["max_slippage_pct"]),
            paper_balance=float(profile["paper_balance"]),
            min_confidence=float(profile.get("min_confidence") or 55.0),
            max_notional_pct=float(profile.get("max_notional_pct") or 35.0),
            symbol_cooldown_min=int(profile.get("symbol_cooldown_min") or 30),
            max_portfolio_exposure_pct=float(profile.get("max_portfolio_exposure_pct") or 70),
            symbol_policy=str(profile.get("symbol_policy") or "ALL"),
            symbol_whitelist=CopyTradingService._json_tuple(profile.get("symbol_whitelist_json")),
            symbol_blacklist=CopyTradingService._json_tuple(profile.get("symbol_blacklist_json")),
            timeframe_filters=CopyTradingService._json_tuple(profile.get("timeframe_filters_json")),
            setup_filters=CopyTradingService._json_tuple(profile.get("setup_filters_json")),
            direction_filters=CopyTradingService._json_tuple(profile.get("direction_filters_json")),
            allow_experimental=bool(profile.get("allow_experimental")),
        )

    def _position_identity_state(self, telegram_id: int) -> tuple[UnifiedOpenPositionState, set[int], int]:
        unified = self.execution_repository.unified_open_state(telegram_id)
        with connect() as conn:
            rows = conn.execute(
                """SELECT p.signal_id
                   FROM paper_positions p
                   JOIN signals s ON s.id=p.signal_id
                   WHERE p.telegram_id=? AND p.status IN ('OPEN','PARTIAL')
                     AND UPPER(COALESCE(s.status,'')) IN ('ACTIVE','TP1','TP2')""",
                (telegram_id,),
            ).fetchall()
        legacy_signal_ids = {int(row[0]) for row in rows if row[0] is not None}
        duplicate_count = len(legacy_signal_ids.intersection(unified.signal_ids))
        hybrid_count = len(rows) + unified.open_count - duplicate_count
        return unified, legacy_signal_ids, hybrid_count

    def _portfolio_state(self, telegram_id: int, symbol: str, cooldown_min: int) -> PortfolioState:
        reconciliation = self.reconciliation.reconcile(telegram_id)
        unified, legacy_signal_ids, hybrid_count = self._position_identity_state(telegram_id)
        heat_by_signal = dict(unified.heat_r_by_signal)
        unified_confirmed_heat = max(
            0.0,
            unified.confirmed_heat_r
            - sum(heat_by_signal.get(signal_id, 0.0) for signal_id in legacy_signal_ids),
        )
        unresolved_unified_count = len(
            unified.unresolved_risk_signal_ids.difference(legacy_signal_ids)
        )
        portfolio_resolved = reconciliation.portfolio_state_resolved and unresolved_unified_count == 0
        normalized_symbol = str(symbol or "").strip().upper()
        cooldown_since = (datetime.now(timezone.utc) - timedelta(minutes=max(0, cooldown_min))).isoformat()
        with connect() as conn:
            symbol_open = conn.execute(
                """SELECT COUNT(*) c
                   FROM paper_positions p
                   JOIN signals s ON s.id=p.signal_id
                   WHERE p.telegram_id=? AND UPPER(TRIM(p.symbol))=? AND p.status IN ('OPEN','PARTIAL')
                     AND UPPER(COALESCE(s.status,'')) IN ('ACTIVE','TP1','TP2')""",
                (telegram_id, normalized_symbol),
            ).fetchone()
            cooldown = conn.execute(
                """SELECT COUNT(*) c FROM paper_positions
                   WHERE telegram_id=? AND UPPER(TRIM(symbol))=? AND status='CLOSED' AND closed_at>=?""",
                (telegram_id, normalized_symbol, cooldown_since),
            ).fetchone()
            daily = conn.execute(
                """SELECT COALESCE(SUM(realized_pnl_delta),0) pnl FROM execution_events
                   WHERE telegram_id=? AND created_at>=? AND event_type IN ('PARTIAL_FILLED','CLOSED')""",
                (telegram_id, _day_start()),
            ).fetchone()
        accounting = self.portfolio_engine.snapshot(telegram_id, cooldown_min=cooldown_min)
        mode = self._accounting_mode()
        unified_symbol_open = normalized_symbol in set(accounting.symbols)
        if mode == "LEGACY":
            return PortfolioState(
                open_positions=reconciliation.legacy_open_count,
                current_heat_r=reconciliation.confirmed_active_heat_r,
                daily_realized_pnl=float(daily[0] or 0.0),
                symbol_is_open=bool(symbol_open[0]), symbol_in_cooldown=bool(cooldown[0]),
                portfolio_state_resolved=reconciliation.portfolio_state_resolved,
                unresolved_legacy_positions=reconciliation.unresolved_legacy_count,
                unresolved_heat_r=reconciliation.unresolved_heat_r,
                heat_source=reconciliation.heat_source,
                reconciliation_status=reconciliation.status,
                legacy_open_positions=reconciliation.legacy_open_count,
                unified_open_positions=accounting.open_positions,
                deduplicated_open_positions=reconciliation.legacy_open_count,
                position_state_source="LEGACY_ROLLBACK",
                unified_symbols=accounting.symbols,
                unified_gross_notional=accounting.gross_notional,
                unified_net_notional=accounting.net_notional,
                unified_unrealized_pnl=accounting.unrealized_pnl,
                unified_realized_pnl=accounting.realized_gross_pnl,
                unified_commission=accounting.commissions,
                unified_confirmed_heat_r=accounting.confirmed_heat_r,
                unified_unresolved_risk_positions=accounting.unresolved_risk_count,
            )
        if mode == "UNIFIED":
            return PortfolioState(
                open_positions=accounting.open_positions,
                current_heat_r=accounting.confirmed_heat_r,
                daily_realized_pnl=accounting.daily_realized_result,
                symbol_is_open=unified_symbol_open,
                symbol_in_cooldown=normalized_symbol in set(accounting.cooldown_symbols),
                portfolio_state_resolved=accounting.resolved,
                unresolved_legacy_positions=accounting.unresolved_risk_count,
                heat_source="UNIFIED_REMAINING_RISK",
                reconciliation_status=reconciliation.status,
                legacy_open_positions=reconciliation.confirmed_active_legacy_count,
                unified_open_positions=accounting.open_positions,
                deduplicated_open_positions=accounting.open_positions,
                position_state_source="UNIFIED",
                unified_symbols=accounting.symbols,
                unified_gross_notional=accounting.gross_notional,
                unified_net_notional=accounting.net_notional,
                unified_unrealized_pnl=accounting.unrealized_pnl,
                unified_realized_pnl=accounting.realized_gross_pnl,
                unified_commission=accounting.commissions,
                unified_confirmed_heat_r=accounting.confirmed_heat_r,
                unified_unresolved_risk_positions=accounting.unresolved_risk_count,
            )
        return PortfolioState(
            open_positions=hybrid_count,
            current_heat_r=reconciliation.confirmed_active_heat_r + unified_confirmed_heat,
            daily_realized_pnl=float(daily[0] or 0.0),
            symbol_is_open=bool(symbol_open[0]) or unified_symbol_open,
            symbol_in_cooldown=bool(cooldown[0]),
            portfolio_state_resolved=portfolio_resolved,
            unresolved_legacy_positions=(
                reconciliation.unresolved_legacy_count + unresolved_unified_count
            ),
            unresolved_heat_r=reconciliation.unresolved_heat_r,
            heat_source=(
                "HYBRID_CONFIRMED"
                if reconciliation.confirmed_active_legacy_count and unified_confirmed_heat
                else "UNIFIED_CONFIRMED"
                if unified_confirmed_heat
                else reconciliation.heat_source
            ),
            reconciliation_status=reconciliation.status,
            legacy_open_positions=reconciliation.confirmed_active_legacy_count,
            unified_open_positions=unified.open_count,
            deduplicated_open_positions=hybrid_count,
            position_state_source="HYBRID_LEGACY_UNIFIED",
            unified_symbols=unified.symbols,
            unified_gross_notional=unified.gross_notional,
            unified_net_notional=unified.net_notional,
            unified_unrealized_pnl=unified.unrealized_pnl,
            unified_realized_pnl=unified.realized_pnl,
            unified_commission=unified.total_commission,
            unified_confirmed_heat_r=unified_confirmed_heat,
            unified_unresolved_risk_positions=unresolved_unified_count,
        )

    def plan_execution(
        self, telegram_id: int, signal: dict[str, Any], *, require_auto_copy: bool = False,
        exchange_account_id: int | None = None, market_price: float | None = None,
    ):
        profile = self.ensure_profile(telegram_id)
        risk_profile = self._risk_profile(profile)
        state = self._portfolio_state(telegram_id, str(signal.get("symbol") or ""), risk_profile.symbol_cooldown_min)
        equity = max(0.0, float(self.profile_stats(telegram_id)["equity"]))
        plan = self.planner.build(
            telegram_id=telegram_id, signal=signal, profile=risk_profile, balance=equity,
            portfolio=state, training_policy=self.training.policy_for(telegram_id, signal),
            market_price=market_price, exchange_account_id=exchange_account_id,
            require_auto_copy=require_auto_copy,
        )
        self.execution_queue.enqueue(plan)
        return plan

    def project_execution(self, idempotency_key: str) -> bool:
        """Materialize the rollback-compatible legacy projection after queue execution."""
        position = self.execution_repository.position_by_idempotency(idempotency_key)
        if not position:
            return False
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM signals WHERE id=?", (position["signal_id"],)
            ).fetchone()
        self._project_unified_position(
            position, signal=dict(row) if row is not None else None
        )
        return True

    def _open(self, telegram_id: int, profile: dict[str, Any], signal: dict[str, Any]) -> str:
        if os.getenv("COPY_EXECUTION_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
            return "SKIPPED"
        risk_profile = self._risk_profile(profile)
        state = self._portfolio_state(telegram_id, str(signal["symbol"]), risk_profile.symbol_cooldown_min)
        stats = self.profile_stats(telegram_id)
        equity = max(0.0, float(stats["equity"]))
        training_policy = self.training.policy_for(telegram_id, signal)
        plan = self.planner.build(
            telegram_id=telegram_id,
            signal=signal,
            profile=risk_profile,
            balance=equity,
            portfolio=state,
            training_policy=training_policy,
            require_auto_copy=True,
        )
        result = self.execution_queue.engine.execute(plan)
        now = _now()
        genome_json, genome_fingerprint = self.similarity.snapshot(signal)
        if not plan.approved or plan.quantity is None or plan.notional is None or plan.risk_amount is None:
            with connect() as conn:
                conn.execute(
                    """INSERT INTO paper_positions(
                           telegram_id,signal_id,symbol,timeframe,side,status,entry_price,last_price,stop_price,
                           rejection_code,rejection_reason,last_signal_status,genome_json,genome_fingerprint,created_at,updated_at
                       ) VALUES(?,?,?,?,?,'REJECTED',?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(telegram_id,signal_id) DO NOTHING""",
                    (telegram_id, signal["id"], signal["symbol"], signal["timeframe"], signal["side"],
                     float(signal.get("entry") or 0.0), float(signal.get("current_price") or signal.get("entry") or 0.0),
                     float(signal.get("stop") or 0.0), plan.code, plan.reason,
                     signal.get("status"), genome_json, genome_fingerprint, now, now),
                )
                self._event_conn(conn, telegram_id, signal["id"], "REJECTED", None, 0.0, {
                    "code": plan.code, "reason": plan.reason, "plan_id": plan.plan_id,
                    "daily_pnl": state.daily_realized_pnl, "heat_r": state.current_heat_r,
                    "training_sample_size": plan.training_sample_size,
                    "training_expectancy_r": training_policy.expectancy_r,
                })
            return "REJECTED"

        if str(getattr(result.status, "value", result.status)) != JournalStatus.EXECUTED.value:
            return "SKIPPED"
        unified = self.execution_repository.position_by_idempotency(plan.idempotency_key)
        if not unified:
            return "SKIPPED"
        initial_event = self.paper_lifecycle.apply_signal_transition(
            int(unified["id"]),
            signal_status=str(signal.get("status") or "ACTIVE"),
            price=float(signal.get("current_price") or signal.get("entry") or unified["average_entry"]),
            event_key=f"position:{int(unified['id'])}:signal:{int(unified['signal_id'])}:"
                      f"{str(signal.get('status') or 'ACTIVE').upper()}",
            reason="INITIAL_SIGNAL_STATE",
        )
        self._project_unified_position(
            initial_event.position,
            signal=signal,
            plan=plan,
            projection_metadata={
                "equity_before": equity,
                "training_expectancy_r": training_policy.expectancy_r,
            },
        )
        return "OPEN"

    def _sync_unified_existing(
        self, position: dict[str, Any], signal: dict[str, Any]
    ) -> str:
        signal_status = str(signal.get("status") or "").upper()
        price = float(
            signal.get("exit_price")
            or signal.get("current_price")
            or position.get("last_price")
            or position.get("average_entry")
            or 0.0
        )
        if price <= 0:
            return "SKIPPED"
        event_key = (
            f"position:{int(position['id'])}:"
            f"signal:{int(position['signal_id'])}:{signal_status}"
        )
        result = self.paper_lifecycle.apply_signal_transition(
            int(position["id"]),
            signal_status=signal_status,
            price=price,
            event_key=event_key,
            reason=str(signal.get("result") or signal_status),
            commission_rate=self.paper_lifecycle.DEFAULT_COMMISSION_RATE,
        )
        self._project_unified_position(result.position, signal=signal, event=result)
        if not result.applied:
            return "SKIPPED"
        return "CLOSED" if result.event_type == "CLOSED" else "UPDATED"

    def _project_unified_position(
        self,
        position: dict[str, Any],
        *,
        signal: dict[str, Any] | None = None,
        plan=None,
        event=None,
        projection_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Backward-compatible legacy projection; unified state is authoritative."""
        signal = signal or {}
        if plan is None:
            journal = self.execution_journal.get(str(position["idempotency_key"]))
            if journal:
                try:
                    payload = json.loads(str(journal.get("plan_json") or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
            else:
                payload = {}
        else:
            payload = {
                "stop_loss": plan.stop_loss,
                "take_profits": list(plan.take_profits),
                "notional": plan.notional,
                "risk_amount": plan.risk_amount,
                "leverage": plan.leverage,
                "expected_slippage_pct": plan.expected_slippage_pct,
                "training_sample_size": plan.training_sample_size,
                "risk_multiplier": plan.risk_multiplier,
                "plan_id": plan.plan_id,
            }
        take_profits = list(payload.get("take_profits") or ())
        take_profits += [None] * (3 - len(take_profits))
        unified_status = str(position.get("status") or "")
        legacy_status = (
            "CLOSED" if unified_status == "CLOSED"
            else "PARTIAL" if unified_status == "PARTIALLY_CLOSED"
            else "OPEN"
        )
        now = _now()
        genome_json, genome_fingerprint = self.similarity.snapshot(signal) if signal else (None, None)
        with connect() as conn:
            existing_projection = conn.execute(
                "SELECT id FROM paper_positions WHERE telegram_id=? AND signal_id=?",
                (position["telegram_id"], position["signal_id"]),
            ).fetchone()
            conn.execute(
                """INSERT INTO paper_positions(
                       telegram_id,signal_id,symbol,timeframe,side,status,entry_price,last_price,
                       stop_price,tp1,tp2,tp3,quantity,notional,risk_amount,initial_risk_r,
                       remaining_fraction,realized_r,realized_pnl,last_signal_status,
                       genome_json,genome_fingerprint,opened_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(telegram_id,signal_id) DO UPDATE SET
                       status=excluded.status,last_price=excluded.last_price,
                       quantity=excluded.quantity,remaining_fraction=excluded.remaining_fraction,
                       realized_r=excluded.realized_r,realized_pnl=excluded.realized_pnl,
                       last_signal_status=excluded.last_signal_status,updated_at=excluded.updated_at""",
                (
                    position["telegram_id"], position["signal_id"], position["symbol"],
                    position["timeframe"], position["side"], legacy_status,
                    position.get("average_entry"), position.get("last_price"),
                    payload.get("stop_loss") or position.get("stop_loss"),
                    take_profits[0], take_profits[1], take_profits[2],
                    position.get("quantity"), payload.get("notional"),
                    payload.get("risk_amount") or position.get("initial_risk_amount"),
                    position.get("remaining_fraction", 1.0), position.get("realized_r", 0.0),
                    position.get("realized_pnl", 0.0),
                    signal.get("status") or position.get("last_signal_status"),
                    genome_json, genome_fingerprint, position.get("opened_at") or now, now, now,
                ),
            )
            if legacy_status == "CLOSED":
                conn.execute(
                    """UPDATE paper_positions SET exit_price=?,close_reason=?,closed_at=?
                       WHERE telegram_id=? AND signal_id=?""",
                    (
                        position.get("last_price"), position.get("close_reason"), position.get("closed_at"),
                        position["telegram_id"], position["signal_id"],
                    ),
                )
            if event is not None and event.event_type in {"CLOSED", "PARTIAL_CLOSED"}:
                event_type = "CLOSED" if event.event_type == "CLOSED" else "PARTIAL_FILLED"
                self._event_conn(
                    conn, int(position["telegram_id"]), int(position["signal_id"]),
                    event_type, float(position.get("last_price") or 0.0),
                    float(event.realized_pnl_delta), {
                        "authoritative_source": "paper_execution_positions",
                        "position_id": position["id"],
                        "realized_r_delta": event.realized_r_delta,
                    },
                    source_event_key=event.event_key,
                )
            elif existing_projection is None:
                metadata = dict(projection_metadata or {})
                metadata.update({
                    "authoritative_source": "paper_execution_positions",
                    "position_id": position["id"],
                    "plan_id": payload.get("plan_id"),
                    "idempotency_key": position["idempotency_key"],
                    "leverage": payload.get("leverage"),
                    "training_sample_size": payload.get("training_sample_size"),
                    "training_risk_multiplier": payload.get("risk_multiplier"),
                })
                self._event_conn(
                    conn, int(position["telegram_id"]), int(position["signal_id"]),
                    "OPENED", float(position.get("average_entry") or 0.0), 0.0, metadata,
                    source_event_key=f"position-open:{position['id']}",
                )

    def _sync_rejected(self, position: dict[str, Any], signal: dict[str, Any]) -> str:
        """Resolve the counterfactual outcome without ever creating exposure or PnL."""
        if position.get("shadow_closed_at"):
            return "SKIPPED"
        signal_status = str(signal.get("status") or "").upper()
        terminal = signal_status in self.TERMINAL_SIGNAL_STATUSES or bool(signal.get("closed_at"))
        if not terminal:
            return "SKIPPED"
        price = float(signal.get("exit_price") or signal.get("current_price") or signal.get("entry") or 0.0)
        entry = float(signal.get("entry") or position.get("entry_price") or 0.0)
        stop = float(signal.get("stop") or position.get("stop_price") or 0.0)
        side = str(signal.get("side") or position.get("side") or "").upper()
        risk = abs(entry - stop)
        shadow_r = 0.0 if risk <= 0 else (((price - entry) if side == "LONG" else (entry - price)) / risk)
        now = _now()
        result = str(signal.get("result") or signal_status)
        with connect() as conn:
            conn.execute(
                """UPDATE paper_positions SET shadow_exit_price=?,shadow_realized_r=?,shadow_result=?,
                   shadow_closed_at=?,last_signal_status=?,updated_at=? WHERE id=?""",
                (price, shadow_r, result, now, signal_status, now, position["id"]),
            )
            self._event_conn(conn, int(position["telegram_id"]), int(position["signal_id"]),
                             "REJECTION_RESOLVED", price, 0.0, {
                                 "rejection_code": position.get("rejection_code") or "UNKNOWN",
                                 "shadow_realized_r": shadow_r, "shadow_result": result,
                                 "diagnostic_only": True,
                             })
        return "UPDATED"

    def _sync_existing(self, position: dict[str, Any], signal: dict[str, Any]) -> str:
        now = _now()
        signal_status = str(signal.get("status") or "").upper()
        if signal_status == str(position.get("last_signal_status") or "").upper() and not signal.get("closed_at"):
            return "SKIPPED"
        price = float(signal.get("exit_price") or signal.get("current_price") or position["last_price"] or position["entry_price"])
        terminal = signal_status in self.TERMINAL_SIGNAL_STATUSES or bool(signal.get("closed_at"))
        if terminal:
            with connect() as conn:
                self._close_position_conn(conn, position, price, str(signal.get("result") or signal_status), now)
            return "CLOSED"

        target_remaining = 1.0
        if signal_status == "TP1":
            target_remaining = 0.5
        elif signal_status == "TP2":
            target_remaining = 0.25
        current_remaining = float(position.get("remaining_fraction") or 0.0)
        if target_remaining >= current_remaining:
            with connect() as conn:
                conn.execute(
                    "UPDATE paper_positions SET last_price=?,last_signal_status=?,updated_at=? WHERE id=?",
                    (price, signal_status, now, position["id"]),
                )
            return "UPDATED"

        closed_fraction = current_remaining - target_remaining
        trade_r = self._r_multiple(position, price)
        realized_r_delta = trade_r * closed_fraction
        realized_pnl_delta = realized_r_delta * float(position.get("risk_amount") or 0.0)
        with connect() as conn:
            conn.execute(
                """UPDATE paper_positions SET status='PARTIAL',last_price=?,remaining_fraction=?,
                   realized_r=COALESCE(realized_r,0)+?,realized_pnl=COALESCE(realized_pnl,0)+?,
                   last_signal_status=?,updated_at=? WHERE id=?""",
                (price, target_remaining, realized_r_delta, realized_pnl_delta, signal_status, now, position["id"]),
            )
            self._event_conn(conn, int(position["telegram_id"]), int(position["signal_id"]),
                             "PARTIAL_FILLED", price, realized_pnl_delta, {
                                 "signal_status": signal_status, "closed_fraction": closed_fraction,
                                 "remaining_fraction": target_remaining, "realized_r_delta": realized_r_delta,
                             })
        return "UPDATED"

    @staticmethod
    def _r_multiple(position: dict[str, Any], price: float) -> float:
        entry = float(position["entry_price"])
        stop = float(position["stop_price"])
        side = str(position["side"]).upper()
        risk = abs(entry - stop)
        if risk <= 0:
            return 0.0
        return ((price - entry) if side == "LONG" else (entry - price)) / risk

    def _close_position_conn(self, conn, position: dict[str, Any], price: float, reason: str, now: str) -> None:
        remaining = float(position.get("remaining_fraction") or 0.0)
        trade_r = self._r_multiple(position, price)
        realized_r_delta = trade_r * remaining
        realized_pnl_delta = realized_r_delta * float(position.get("risk_amount") or 0.0)
        total_r = float(position.get("realized_r") or 0.0) + realized_r_delta
        total_pnl = float(position.get("realized_pnl") or 0.0) + realized_pnl_delta
        conn.execute(
            """UPDATE paper_positions SET status='CLOSED',last_price=?,exit_price=?,remaining_fraction=0,
               realized_r=?,realized_pnl=?,close_reason=?,last_signal_status=?,closed_at=?,updated_at=? WHERE id=?""",
            (price, price, total_r, total_pnl, reason, reason, now, now, position["id"]),
        )
        self._event_conn(conn, int(position["telegram_id"]), int(position["signal_id"]),
                         "CLOSED", price, realized_pnl_delta, {
                             "reason": reason, "remaining_fraction": remaining,
                             "realized_r_delta": realized_r_delta, "total_realized_r": total_r,
                             "total_realized_pnl": total_pnl,
                         })

    @staticmethod
    def _event_conn(
        conn,
        telegram_id: int,
        signal_id: int,
        event_type: str,
        price: float | None,
        realized_pnl_delta: float,
        details: dict[str, Any],
        source_event_key: str | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO execution_events(
                   telegram_id,signal_id,event_type,price,realized_pnl_delta,details_json,
                   source_event_key,created_at
               ) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(source_event_key) DO NOTHING""",
            (telegram_id, signal_id, event_type, price, realized_pnl_delta,
             json.dumps(details, ensure_ascii=False), source_event_key, _now()),
        )

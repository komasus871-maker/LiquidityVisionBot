from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from database.database import connect, database_backend, get_runtime_states, persistent_database, ping_database

from version import APP_VERSION
from services.execution_repositories import ExecutionRepository
from services.live_readiness import configured_mode
from services.ai_trading import configured_capabilities
from services.providers.okx import OKXProvider
from services.market_intelligence_repository import MarketIntelligenceRepository
_STARTED_AT = datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _scalar(conn, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(next(iter(row.values())) or 0)
    return int(row[0] or 0)


def collect_runtime_diagnostics(*, stale_after_seconds: int | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    stale_after = stale_after_seconds or int(os.getenv("WORKER_STALE_AFTER", "900"))
    db = ping_database()

    workers: list[dict[str, Any]] = []
    stale_workers: list[str] = []
    for state in get_runtime_states():
        item = dict(state)
        success_at = _parse_time(item.get("last_success_at"))
        age = int((now - success_at).total_seconds()) if success_at else None
        started_at = _parse_time(item.get("last_started_at"))
        finished_at = _parse_time(item.get("last_finished_at"))
        started_age = int((now - started_at).total_seconds()) if started_at else None
        running = bool(started_at and (not finished_at or started_at > finished_at))
        cycle_seconds = None
        if started_at and finished_at and finished_at >= started_at:
            cycle_seconds = round((finished_at - started_at).total_seconds(), 2)
        item["age_seconds"] = age
        item["started_age_seconds"] = started_age
        item["running"] = running
        item["cycle_seconds"] = cycle_seconds
        item["stale"] = (running and started_age is not None and started_age > stale_after) or (not running and (age is None or age > stale_after))
        try:
            item["details"] = json.loads(item.get("details_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            item["details"] = {}
        item.pop("details_json", None)
        configured_enabled = not (
            item.get("worker_name") == "microstructure_observer" and
            os.getenv("MICROSTRUCTURE_COLLECTION_ENABLED", "false").strip().lower()
            not in {"1", "true", "yes", "on"}
        )
        item["enabled"] = configured_enabled
        item["configuration_reason"] = (
            "ENABLED_BY_CONFIGURATION" if configured_enabled else "DISABLED_BY_CONFIGURATION")
        item["health_status"] = (
            "DISABLED" if not configured_enabled else
            "FAILED" if item.get("last_error") and int(item.get("error_count") or 0) > 0 else
            "DEGRADED" if item["stale"] else "HEALTHY"
        )
        workers.append(item)
        if item["stale"]:
            stale_workers.append(str(item.get("worker_name")))

    if not any(item.get("worker_name") == "microstructure_observer" for item in workers):
        micro_enabled = os.getenv("MICROSTRUCTURE_COLLECTION_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on"}
        workers.append({
            "worker_name": "microstructure_observer", "enabled": micro_enabled,
            "configuration_reason": ("ENABLED_BY_CONFIGURATION" if micro_enabled else
                                     "DISABLED_BY_CONFIGURATION"),
            "health_status": "NOT_STARTED" if micro_enabled else "DISABLED",
            "stale": bool(micro_enabled), "running": False, "age_seconds": None,
            "cycle_seconds": None, "processed_count": 0, "error_count": 0,
            "last_error": None, "details": {},
        })

    micro_health = MarketIntelligenceRepository().worker_health() or {}
    configured_micro = os.getenv("MICROSTRUCTURE_COLLECTION_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"}
    for item in workers:
        if item.get("worker_name") != "microstructure_observer":
            continue
        heartbeat = _parse_time(micro_health.get("heartbeat_at"))
        heartbeat_age = int((now - heartbeat).total_seconds()) if heartbeat else None
        source_times = [_parse_time(micro_health.get(key)) for key in (
            "last_depth_success_at", "last_funding_success_at", "last_oi_success_at")]
        source_times = [value for value in source_times if value]
        snapshot_age = int((now - max(source_times)).total_seconds()) if source_times else None
        state = str(micro_health.get("state") or (
            "NOT_STARTED" if configured_micro else "DISABLED_BY_CONFIGURATION"))
        if not configured_micro:
            state = "DISABLED_BY_CONFIGURATION"
        elif not micro_health.get("worker_started_at"):
            state = "NOT_STARTED"
        elif heartbeat_age is None or heartbeat_age > stale_after:
            state = "STALE"
        item.update({
            "enabled": configured_micro,
            "effective_enabled": bool(micro_health.get("effective_enabled", configured_micro)),
            "configuration_reason": ("ENABLED_BY_CONFIGURATION" if configured_micro
                                     else "DISABLED_BY_CONFIGURATION"),
            "health_status": state, "stale": state == "STALE",
            "age_seconds": heartbeat_age, "snapshot_age_seconds": snapshot_age,
            "details": {**(item.get("details") or {}), **micro_health},
        })
        if state == "STALE" and "microstructure_observer" not in stale_workers:
            stale_workers.append("microstructure_observer")

    with connect() as conn:
        counts = {
            "users": _scalar(conn, "SELECT COUNT(*) FROM users"),
            "watchlist_items": _scalar(conn, "SELECT COUNT(*) FROM user_watchlist"),
            "observations": _scalar(conn, "SELECT COUNT(*) FROM analysis_observations"),
            "open_signals": _scalar(conn, "SELECT COUNT(*) FROM signals WHERE status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')"),
            "active_trades": _scalar(conn, "SELECT COUNT(*) FROM signals WHERE status IN ('ACTIVE','TP1','TP2')"),
            "closed_signals": _scalar(conn, "SELECT COUNT(*) FROM signals WHERE status IN ('TP3','STOP','BREAKEVEN','MANUAL_STOP','CLOSED','INVALIDATED','EXPIRED')"),
            "watch_errors": _scalar(conn, "SELECT COUNT(*) FROM watch_states WHERE consecutive_errors > 0"),
            "execution_retry_wait": _scalar(conn, "SELECT COUNT(*) FROM copy_execution_journal WHERE status='RETRY_WAIT'"),
            "execution_dead_letter": _scalar(conn, "SELECT COUNT(*) FROM copy_execution_journal WHERE status='DEAD_LETTER'"),
            "execution_claimed": _scalar(conn, "SELECT COUNT(*) FROM copy_execution_journal WHERE status='EXECUTING'"),
            "unified_open_positions": _scalar(conn, "SELECT COUNT(*) FROM paper_execution_positions WHERE status IN ('OPEN','PARTIALLY_FILLED','PARTIALLY_CLOSED')"),
            "portfolio_ledger_entries": _scalar(conn, "SELECT COUNT(*) FROM paper_portfolio_ledger"),
            "historical_migrated": _scalar(conn, "SELECT COUNT(*) FROM historical_execution_records"),
            "historical_unresolved": _scalar(conn, "SELECT COUNT(*) FROM historical_execution_records WHERE migration_status='UNRESOLVED'"),
            "live_unknown": _scalar(conn, "SELECT COUNT(*) FROM live_executions WHERE state='UNKNOWN'"),
            "live_recovery_required": _scalar(conn, "SELECT COUNT(*) FROM live_executions WHERE state='RECOVERY_REQUIRED'"),
            "live_retry_wait": _scalar(conn, "SELECT COUNT(*) FROM live_executions WHERE state='RETRY_WAIT'"),
            "exchange_connections": _scalar(conn, "SELECT COUNT(*) FROM user_exchange_credentials WHERE status='connected'"),
            "live_accounts": _scalar(conn, "SELECT COUNT(*) FROM live_exchange_accounts"),
            "live_enabled_accounts": _scalar(conn, "SELECT COUNT(*) FROM live_exchange_accounts WHERE live_enabled=1"),
            "live_risk_profiles_active": _scalar(conn, "SELECT COUNT(*) FROM live_risk_profiles WHERE status='ACTIVE'"),
            "live_reconciliation_unresolved": _scalar(conn, "SELECT COUNT(*) FROM live_reconciliation_events WHERE resolved_at IS NULL"),
            "live_order_intents": _scalar(conn, "SELECT COUNT(*) FROM live_order_intents"),
            "bingx_certification_passed": _scalar(conn, "SELECT COUNT(*) FROM bingx_certification_audits WHERE status='VST_ECONOMIC_PASSED'"),
            "bingx_certification_running": _scalar(conn, "SELECT COUNT(*) FROM bingx_certification_audits WHERE status='VST_ECONOMIC_RUNNING'"),
            "ai_decisions": _scalar(conn, "SELECT COUNT(*) FROM ai_decisions"),
            "ai_invalid_responses": _scalar(conn, "SELECT COUNT(*) FROM ai_decisions WHERE schema_valid=0"),
            "ai_timeouts": _scalar(conn, "SELECT COUNT(*) FROM ai_decisions WHERE validation_code='PROVIDER_TIMEOUT'"),
            "ai_cost_limit_blocks": _scalar(conn, "SELECT COUNT(*) FROM ai_decisions WHERE validation_code IN ('COST_LIMIT','DAILY_REQUEST_LIMIT')"),
            "ai_stale_context_rejects": _scalar(conn, "SELECT COUNT(*) FROM ai_decisions WHERE validation_code='STALE_CONTEXT'"),
            "ai_unmatched_outcomes": _scalar(conn, "SELECT COUNT(*) FROM ai_decisions d LEFT JOIN ai_decision_outcomes o ON o.decision_id=d.decision_id WHERE o.decision_id IS NULL"),
            "ai_stale_request_claims": _scalar(conn, "SELECT COUNT(*) FROM ai_request_claims WHERE expires_at<?", (now.isoformat(),)),
        }
        duplicate_open_plans = _scalar(conn, """
            SELECT COUNT(*) FROM (
                SELECT COALESCE(owner_telegram_id,0), symbol, timeframe
                FROM signals
                WHERE status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')
                GROUP BY COALESCE(owner_telegram_id,0), symbol, timeframe
                HAVING COUNT(*) > 1
            ) x
        """)
        watch_error_rows = [dict(row) for row in conn.execute("""
            SELECT telegram_id, symbol, timeframe, consecutive_errors, last_error, last_checked_at
            FROM watch_states WHERE consecutive_errors > 0
            ORDER BY consecutive_errors DESC, updated_at DESC LIMIT 10
        """).fetchall()]
        impossible_active = _scalar(conn, """
            SELECT COUNT(*) FROM signals
            WHERE status IN ('ACTIVE','TP1','TP2')
              AND (activated_at IS NULL OR effective_stop IS NULL)
        """)
        ai_provider_rows = [dict(row) for row in conn.execute(
            "SELECT provider,state,consecutive_failures,opened_until,last_success_at,last_failure_at,last_error_code FROM ai_provider_state"
        ).fetchall()]

    integrity = {
        "ok": duplicate_open_plans == 0 and impossible_active == 0,
        "duplicate_open_plans": duplicate_open_plans,
        "active_without_activation_or_stop": impossible_active,
    }
    lifecycle_integrity = ExecutionRepository().lifecycle_integrity()
    integrity.update(lifecycle_integrity)
    integrity["ok"] = (
        bool(integrity["ok"])
        and lifecycle_integrity["duplicate_open_positions"] == 0
        and lifecycle_integrity["closed_with_quantity"] == 0
        and lifecycle_integrity["quantity_fraction_mismatch"] == 0
    )
    status = "ok"
    if not db.get("ok") or not integrity["ok"]:
        status = "degraded"
    elif stale_workers:
        status = "warning"

    return {
        "status": status,
        "service": "Liquidity Vision Intelligence",
        "version": APP_VERSION,
        "portfolio_accounting": {
            "authority": "UNIFIED_POSITIONS+PORTFOLIO_LEDGER",
            "admission_mode": os.getenv("PORTFOLIO_ACCOUNTING_SOURCE", "SHADOW").strip().upper(),
            "daily_timezone": "UTC",
        },
        "execution_mode": configured_mode().value,
        "ai": {
            "mode": os.getenv("AI_TRADING_MODE", "AI_SHADOW").strip().upper(),
            "provider": os.getenv("AI_PROVIDER", "disabled").strip().lower(),
            "provider_protocol": os.getenv("AI_PROVIDER_PROTOCOL", "chat_completions").strip().lower(),
            "requested_output_mode": os.getenv("AI_STRUCTURED_OUTPUT_MODE", "auto").strip().lower(),
            "schema_version": os.getenv("AI_SCHEMA_VERSION", "ai-decision-v3").strip(),
            "max_concurrency": max(1, int(os.getenv("AI_MAX_CONCURRENCY", "2"))),
            "capabilities": asdict(configured_capabilities(os.getenv("AI_PROVIDER_PROTOCOL", "chat_completions").strip().lower())),
            "provider_state": ai_provider_rows,
        },
        "market_data": {
            "primary_provider": OKXProvider.health_snapshot(),
            "microstructure_enabled": os.getenv("MICROSTRUCTURE_COLLECTION_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"},
            "microstructure_configuration": (
                "ENABLED_BY_CONFIGURATION" if os.getenv("MICROSTRUCTURE_COLLECTION_ENABLED", "false").strip().lower()
                in {"1", "true", "yes", "on"} else "DISABLED_BY_CONFIGURATION"),
            "required_render_variable": "MICROSTRUCTURE_COLLECTION_ENABLED=true",
        },
        "live_feature_flag": os.getenv("LIVE_EXECUTION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        "live": {
            "global_execution_enabled": os.getenv("LIVE_EXECUTION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
            "bingx_exchange_enabled": os.getenv("LIVE_EXCHANGE_BINGX_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
            "production_adapter_allowed": os.getenv("BINGX_PRODUCTION_ADAPTER_ALLOWED", "false").strip().lower() in {"1", "true", "yes", "on"},
            "default_state": "DISABLED_BY_DEFAULT",
            "ai_execution_authority": False, "research_execution_authority": False,
        },
        "environment": os.getenv("RENDER_SERVICE_NAME") or os.getenv("ENVIRONMENT", "local"),
        "database_backend": database_backend(),
        "persistent_database": persistent_database(),
        "database": db,
        "counts": counts,
        "watch_errors": watch_error_rows,
        "integrity": integrity,
        "workers": workers,
        "stale_workers": stale_workers,
        "worker_stale_after_seconds": stale_after,
        "uptime_seconds": max(0, int((now - _STARTED_AT).total_seconds())),
        "python": platform.python_version(),
        "timestamp": now.isoformat(),
    }

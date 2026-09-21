"""Executable product/runtime inventory used by release audits and documentation."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from services.command_catalog import FUNCTION_REGISTRY
from services.pump_dump_scanner import DISCOVERY_MODES, SCANNER_ALERT_TYPES


REPLY_CONTROLS = {
    "📊 Markets": "market_now", "🔍 Analyze": "analyze", "⚡ Scanner": "pump_scan",
    "🎯 Signals": "signal_rankings", "🌊 Order Flow": "orderflow", "📈 Paper": "copy",
    "🧪 Shadow Lab": "shadow_status", "💼 Portfolio": "portfolio", "🛡 Risk": "portfolio",
    "🔔 Alerts": "alerts", "⚙ Settings": "settings", "🩺 System": "terminal_health",
    "🚀 Open Terminal": "terminal",
}

TERMINAL_PAGES = (
    "overview", "markets", "scanner", "signals", "order-flow", "derivatives",
    "paper", "portfolio", "risk", "alerts", "shadow", "economics", "system",
)

CATEGORY_TABLES = {
    "market": ("forward_market_state", "market_intelligence_snapshots", "signals"),
    "scanner": ("scanner_user_settings", "scanner_episode_state", "market_anomaly_events",
                "scanner_outcome_labels"),
    "watchlist": ("user_watchlist", "watch_states", "watch_events"),
    "alerts": ("user_preferences", "intelligence_alert_events", "market_anomaly_events"),
    "trading": ("signals", "signal_events", "trade_memories"),
    "copy": ("copy_profiles", "copy_execution_journal", "paper_execution_positions"),
    "intelligence": ("market_intelligence_snapshots", "ai_decisions"),
    "research": ("research_signal_snapshots", "research_outcomes", "research_findings"),
    "system": ("runtime_state", "operational_worker_health", "forward_worker_health"),
    "account": ("users", "user_preferences", "user_exchange_credentials"),
    "settings": ("user_preferences",), "premium": ("user_plan_assignments",),
    "live": ("live_exchange_accounts", "live_executions", "live_positions"),
    "ai": ("ai_decisions", "ai_provider_state"),
}

OPERATIONAL_COMMANDS = {
    "watchlist", "journal", "trade", "closeall", "positions", "performance", "portfolio",
    "copy", "copy_queue", "copy_plan", "orders", "execution", "fills", "panic",
    "pump_scan", "scanner_settings", "alerts", "signal_rankings", "rankings",
}
FORWARD_COMMANDS = {
    "market_now", "orderflow", "shadow_status", "terminal_health", "terminal",
    "funding", "open_interest", "orderbook", "data_health",
}

BACKGROUND_COMPONENTS = (
    ("worker.signal_tracker", "SignalTracker", "B.CONTINUOUS_PRODUCT_OPERATION",
     "liquidityvision-operational-worker", ("signals", "signal_events")),
    ("worker.observation_monitor", "ObservationMonitor", "B.CONTINUOUS_PRODUCT_OPERATION",
     "liquidityvision-operational-worker", ("signals", "observation_history")),
    ("worker.watch_engine", "WatchEngine", "B.CONTINUOUS_PRODUCT_OPERATION",
     "liquidityvision-operational-worker", ("user_watchlist", "watch_states", "watch_events")),
    ("worker.copy_execution", "CopyExecutionWorker", "B.CONTINUOUS_PRODUCT_OPERATION",
     "liquidityvision-operational-worker", ("paper_execution_positions", "paper_order_events")),
    ("worker.ai_shadow", "AIShadowWorker", "B.CONTINUOUS_PRODUCT_OPERATION",
     "liquidityvision-operational-worker", ("ai_decisions", "ai_provider_state")),
    ("worker.research", "ResearchWorker", "B.CONTINUOUS_PRODUCT_OPERATION",
     "liquidityvision-operational-worker", ("research_signal_snapshots", "research_outcomes")),
    ("worker.anomaly_radar", "PumpDumpMonitor", "B.CONTINUOUS_PRODUCT_OPERATION",
     "liquidityvision-operational-worker", ("scanner_episode_state", "market_anomaly_events")),
    ("maintenance.retention", "OperationalMaintenanceWorker", "D.PERIODIC_MAINTENANCE",
     "liquidityvision-operational-worker", ("operational_retention_runs",)),
    ("worker.forward_microstructure", "Forward collector and frozen Shadow lab", "C.FORWARD_RESEARCH",
     "liquidityvision-forward-worker", ("forward_market_state", "forward_worker_health")),
    ("migration.product", "Schema migration and bounded product backfills", "E.ONE_TIME_MIGRATION",
     "LiquidityVisionBot-1 pre-deploy",
     ("schema_migrations", "historical_migration_runs", "historical_execution_records", "trade_memories")),
)


def _callback_patterns(decorator: ast.Call) -> tuple[str, ...]:
    patterns: set[str] = set()
    for nested in ast.walk(decorator):
        if (isinstance(nested, ast.Call) and isinstance(nested.func, ast.Attribute)
                and nested.func.attr == "startswith" and nested.args
                and isinstance(nested.args[0], ast.Constant)
                and isinstance(nested.args[0].value, str)):
            patterns.add(f"{nested.args[0].value}*")
        elif isinstance(nested, ast.Compare):
            for comparator in nested.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    patterns.add(comparator.value)
    return tuple(sorted(patterns))


def _handler_index(root: str | Path) -> tuple[
    dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, tuple[str, ...]],
]:
    commands: dict[str, dict[str, Any]] = {}
    callbacks: dict[str, dict[str, Any]] = {}
    imports: dict[str, tuple[str, ...]] = {}
    for path in sorted(Path(root).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules = sorted({
            node.module for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("services.")
        })
        imports[str(path).replace("\\", "/")] = tuple(modules)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr == "callback_query":
                    for pattern in _callback_patterns(decorator):
                        callbacks[pattern] = {
                            "handler": node.name,
                            "handler_file": str(path).replace("\\", "/"),
                        }
                    continue
                if func.attr != "message":
                    continue
                for nested in ast.walk(decorator):
                    if not isinstance(nested, ast.Call):
                        continue
                    name = nested.func.id if isinstance(nested.func, ast.Name) else ""
                    if name != "Command":
                        continue
                    for arg in nested.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            commands[arg.value] = {
                                "handler": node.name,
                                "handler_file": str(path).replace("\\", "/"),
                            }
    return commands, callbacks, imports


def _runtime_dependency(command: str | None, category: str) -> tuple[str, ...]:
    dependencies: list[str] = []
    if command in OPERATIONAL_COMMANDS or category in {"watchlist", "alerts", "copy"}:
        dependencies.append("liquidityvision-operational-worker")
    if command in FORWARD_COMMANDS:
        dependencies.append("liquidityvision-forward-worker")
    return tuple(dependencies)


def full_functionality_matrix(handler_root: str | Path = "handlers") -> list[dict[str, Any]]:
    handlers, callbacks, imports = _handler_index(handler_root)
    rows: list[dict[str, Any]] = []
    for item in FUNCTION_REGISTRY:
        route = (handlers.get(item.command or "", {}) if item.command else
                 callbacks.get(item.callback_entry or "", {}))
        handler_file = route.get("handler_file")
        rows.append({
            "id": item.id,
            "entry_point": f"/{item.command}" if item.command else item.callback_entry,
            "handler": route.get("handler") or "callback router (filter-derived)",
            "handler_file": handler_file,
            "service_layer": imports.get(handler_file, ()) if handler_file else (),
            "data_source": "bounded PostgreSQL and/or public market provider",
            "state_owner": "PostgreSQL" if CATEGORY_TABLES.get(item.category) else "request-local",
            "persistent_background_dependency": _runtime_dependency(item.command, item.category),
            "database_tables": CATEGORY_TABLES.get(item.category, ()),
            "expected_output": item.description,
            "failure_mode": "explicit unavailable/degraded/error response; no fabricated state",
            "fallback": "retry, /help, or bounded cached/current state where freshness permits",
            "runtime_class": "A.INTERACTIVE_WEB_REQUEST",
            "runtime_owner": "liquidityvisionbot-1",
            "permissions": item.permissions,
            "economic_authority": False if item.category in {"scanner", "research", "ai"} else None,
        })
    for index, (label, command) in enumerate(REPLY_CONTROLS.items(), start=1):
        target = next(row for row in rows if row["entry_point"] == f"/{command}")
        rows.append({**target, "id": f"reply.{index:02d}.{command}", "entry_point": label})
    for page in TERMINAL_PAGES:
        rows.append({
            "id": f"terminal.{page}", "entry_point": f"/api/terminal/{page}",
            "handler": "WebhookServer.terminal_api_handler",
            "handler_file": "services/webhook_server.py",
            "service_layer": ("services.forward_runtime_state", "database.database"),
            "data_source": "bounded authenticated current state",
            "state_owner": "PostgreSQL", "persistent_background_dependency": (
                "liquidityvision-forward-worker", "liquidityvision-operational-worker",
            ),
            "database_tables": CATEGORY_TABLES["system"],
            "expected_output": f"authenticated read-only {page} page",
            "failure_mode": "HTTP 403 invalid/expired auth; explicit empty/degraded state",
            "fallback": "equivalent Telegram command",
            "runtime_class": "A.INTERACTIVE_WEB_REQUEST",
            "runtime_owner": "liquidityvisionbot-1", "permissions": "TELEGRAM_INIT_DATA",
            "economic_authority": False,
        })
    for mode in DISCOVERY_MODES:
        rows.append({
            "id": f"scanner.mode.{mode.lower()}", "entry_point": f"/pump_scan {mode.lower()}",
            "handler": "pump_scan", "handler_file": "handlers/pump_scanner.py",
            "service_layer": ("services.pump_dump_scanner",),
            "data_source": "bounded persisted anomaly episodes", "state_owner": "PostgreSQL",
            "persistent_background_dependency": ("liquidityvision-operational-worker",),
            "database_tables": ("market_anomaly_events", "scanner_episode_state"),
            "expected_output": f"evidence-ranked {mode.replace('_', ' ').lower()} view",
            "failure_mode": "explicit no-episodes or degraded-data response",
            "fallback": "/pump_scan hot_now", "runtime_class": "A.INTERACTIVE_WEB_REQUEST",
            "runtime_owner": "liquidityvisionbot-1", "permissions": "PUBLIC",
            "economic_authority": False,
        })
    for alert_type in SCANNER_ALERT_TYPES:
        rows.append({
            "id": f"scanner.alert.{alert_type.lower()}", "entry_point": alert_type,
            "handler": "PumpDumpMonitor._check_once_owned",
            "handler_file": "services/pump_dump_monitor.py",
            "service_layer": ("services.pump_dump_scanner", "services.forward_runtime_state"),
            "data_source": "stage-1 public radar plus shortlisted forward enrichment",
            "state_owner": "PostgreSQL", "persistent_background_dependency": (
                "liquidityvision-operational-worker", "liquidityvision-forward-worker",
            ),
            "database_tables": ("scanner_user_settings", "market_anomaly_events"),
            "expected_output": "deduplicated descriptive market alert",
            "failure_mode": "quality gate rejects stale, incomplete, or illiquid evidence",
            "fallback": "persist no alert; expose degraded source state",
            "runtime_class": "B.CONTINUOUS_PRODUCT_OPERATION",
            "runtime_owner": "liquidityvision-operational-worker", "permissions": "USER_SETTINGS",
            "economic_authority": False,
        })
    for identity, component, runtime_class, owner, tables in BACKGROUND_COMPONENTS:
        rows.append({
            "id": identity, "entry_point": "background runtime", "handler": component,
            "handler_file": "tools/run_operational_worker.py" if "operational" in owner else (
                "tools/run_forward_microstructure_collector.py" if "forward" in owner
                else "tools/run_product_migrations.py"
            ),
            "service_layer": (), "data_source": "bounded current state and owned public inputs",
            "state_owner": "PostgreSQL" if "forward" not in owner else "PostgreSQL + /var/data",
            "persistent_background_dependency": (owner,), "database_tables": tables,
            "expected_output": component, "failure_mode": "isolated cycle error and health heartbeat",
            "fallback": "retry on next bounded cycle or Render restart from durable state",
            "runtime_class": runtime_class, "runtime_owner": owner,
            "permissions": "INTERNAL", "economic_authority": False,
        })
    return rows


def runtime_ownership_matrix() -> tuple[dict[str, Any], ...]:
    return (
        {
            "owner": "liquidityvisionbot-1", "class": "A.INTERACTIVE_WEB_REQUEST",
            "command": "python bot.py", "singleton": True,
            "responsibilities": ("Telegram webhook", "HTTP health/API", "Mini App", "interactive requests"),
            "forbidden": ("continuous product loops", "forward raw collection", "LIVE execution"),
        },
        {
            "owner": "liquidityvision-operational-worker", "class": "B.CONTINUOUS_PRODUCT_OPERATION",
            "command": "python -m tools.run_operational_worker", "singleton": True,
            "responsibilities": (
                "signals", "observations", "watchlists", "PAPER copy/lifecycle", "alerts",
                "anomaly radar", "research projections", "retention",
            ),
            "forbidden": ("Telegram polling/webhook", "forward raw collection", "LIVE execution"),
        },
        {
            "owner": "liquidityvision-forward-worker", "class": "C.FORWARD_RESEARCH",
            "command": "python -m tools.run_forward_microstructure_collector", "singleton": True,
            "responsibilities": ("public feeds", "raw partitions", "bounded current state", "frozen shadow"),
            "forbidden": ("Telegram authority", "production strategy authority", "LIVE execution"),
        },
        {
            "owner": "LiquidityVisionBot-1 pre-deploy", "class": "E.ONE_TIME_MIGRATION",
            "command": "python -m tools.run_product_migrations", "singleton": True,
            "responsibilities": (
                "advisory-lock-protected schema DDL", "historical execution migration",
                "trade-memory backfill",
            ),
            "forbidden": ("service runtime", "continuous execution"),
        },
    )

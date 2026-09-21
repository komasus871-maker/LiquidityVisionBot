from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class CommandEntry:
    command: str
    summary: str
    usage: str | None = None


def _entries(*items: tuple[str, str] | tuple[str, str, str]) -> tuple[CommandEntry, ...]:
    return tuple(CommandEntry(*item) for item in items)


HELP_CATALOG: dict[str, tuple[CommandEntry, ...]] = {
    "market": _entries(
        ("analyze", "Run deterministic multi-factor analysis", "/analyze BTC 1h"),
        ("deep_analyze", "Deep multi-source analysis with shadow confirmation", "/deep_analyze BTC 1h"),
        ("price", "Current public market price", "/price BTC"),
        ("scanner", "Rank the bounded opportunity universe", "/scanner breakout"),
        ("market", "Compact market overview"), ("news", "Market news feed"),
        ("fear", "Fear and Greed context"),
        ("market_story", "Decision-time market narrative", "/market_story 434"),
        ("signal_quality", "Signal Quality V4", "/signal_quality 434"),
        ("liquidity_map", "Unresolved and consumed liquidity", "/liquidity_map BTCUSDT"),
        ("orderbook", "Bounded order-book intelligence", "/orderbook BTCUSDT"),
        ("funding", "Public funding snapshot", "/funding BTCUSDT"),
        ("open_interest", "Public open-interest snapshot", "/open_interest BTCUSDT"),
        ("data_health", "Data availability and remediation", "/data_health BTCUSDT"),
        ("market_now", "Live BTC/ETH/SOL terminal state"),
        ("orderflow", "Cross-venue order flow", "/orderflow BTCUSDT"),
        ("pump_reversals", "Pump reversal research feed"),
        ("terminal", "Open the read-only Telegram Web Terminal"),
    ),
    "trading": _entries(
        ("watchlist", "View or edit your smart watchlist", "/watchlist add BTC SOL"),
        ("journal", "Trade journal and lifecycle summary"),
        ("trade", "Trade detail, replay, or lifecycle action", "/trade 434"),
        ("closeall", "Close all active PAPER journal trades"),
        ("positions", "Current PAPER copy positions"),
        ("performance", "Expectancy, PF, and lifecycle outcomes"),
        ("portfolio", "Risk and concentration view"),
        ("dna", "Strong and weak historical cohorts"),
        ("insights", "Compact intelligence brief"),
        ("cancel", "Cancel an active symbol prompt"),
    ),
    "copy": _entries(
        ("copy", "PAPER copy overview"), ("copy_profile", "Copy profile"),
        ("copy_symbols", "Allowed symbol universe"), ("copy_filters", "Admission filters"),
        ("copy_enable", "Enable PAPER copy"), ("copy_disable", "Disable copy"),
        ("copy_risk", "Risk setting"), ("copy_size", "Sizing policy"),
        ("copy_leverage", "Bounded leverage setting"), ("copy_auto", "Automation policy"),
        ("copy_balance", "PAPER balance"), ("copy_limits", "Safety limits"),
        ("copy_stats", "Copy statistics"), ("copy_diagnostics", "Copy diagnostics"),
        ("copy_performance", "Candidates, execution, costs, and outcome quality"),
        ("copy_analytics", "PAPER cohorts by strategy, timeframe, symbol, Quality and Readiness"),
        ("copy_guard", "Safety guard state"), ("copy_training", "Training summary"),
        ("copy_rejections", "Admission rejection analytics"),
        ("copy_guardrails", "Effective copy guardrails"),
        ("copy_similar", "Similar historical copy cases"), ("genome", "Copy policy genome"),
        ("copy_queue", "Durable queue state"), ("copy_plan", "Decision-to-order plan"),
        ("orders", "PAPER orders"), ("execution", "Execution events"),
        ("fills", "PAPER fills"), ("panic", "Fail-closed copy stop"),
    ),
    "intelligence": _entries(
        ("signal_rankings", "Signal Ranking V5"), ("contradictions", "Supports and conflicts", "/contradictions 434"),
        ("regimes", "Regime diagnostics"), ("ai_status", "AI advisory status"),
        ("ai_decision", "AI decision for a signal", "/ai_decision 434"),
        ("ai_explain", "Alias for AI decision", "/ai_explain 434"),
        ("ai_metrics", "AI observation metrics"), ("ai_compare", "Deterministic/AI comparison"),
        ("ai_quality", "AI quality diagnostics"), ("ai_dashboard", "AI cost and reuse dashboard"),
        ("ai_history", "Recent AI observations"), ("ai_abstentions", "Genuine versus fallback abstentions"),
        ("ai_failures", "Validation/provider/circuit failures"), ("ai_regimes", "AI outcomes by regime"),
        ("ai_similarity", "Similarity diagnostics"), ("ai_learning", "Bounded learning summary"),
        ("ai_statistics", "AI statistical report"), ("ai_counterfactual", "Counterfactual report"),
        ("ai_provider_health", "Provider health"), ("ai_cost", "Provider cost report"),
        ("capabilities", "Effective entitlement capabilities"),
    ),
    "research": _entries(
        ("research", "Research command index"), ("strategy_lab", "Versioned strategy comparison"),
        ("strategy_compare", "Alias for Strategy Lab"), ("edge_report", "Research edge report"),
        ("edge_discovery", "Leakage-controlled discovery"), ("feature_edge", "Controlled feature comparisons"),
        ("hypotheses", "Frozen hypothesis lifecycle"), ("forward_tests", "Forward-only validation"),
        ("rr_research", "Risk/reward research"), ("exit_research", "Exit-policy research"),
        ("confidence_research", "Confidence calibration"), ("portfolio_edge", "Portfolio overlap research"),
        ("scalping_research", "After-cost scalping research"),
        ("ai_research_compare", "Advisory red-team comparison"),
        ("entry_research", "Entry timing research"), ("reentry_research", "Re-entry research"),
        ("quality_report", "Quality cohorts and missed winners"),
        ("quality_cohorts", "Exception cohorts for calibration and opportunity cost"),
        ("strategy_distribution", "Observed strategy-family assignment distribution"),
        ("export_analytics", "Export your own aggregate PAPER analytics as JSON or CSV",
         "/export_analytics json 90"),
    ),
    "system": _entries(
        ("system_health", "System Health V3"),
        ("terminal_health", "Telegram and forward-worker status"),
        ("shadow_status", "Forward Shadow Lab coverage without outcome metrics"),
    ),
    "account": _entries(
        ("profile", "User profile"), ("premium", "Plan overview"),
        ("plans", "Free, Pro, and Elite comparison"), ("my_plan", "Effective plan and limits"),
        ("settings", "Personal output preferences"), ("alerts", "Notification preferences"),
        ("language", "Select English, Russian, Ukrainian, Hebrew, or Arabic"),
        ("usage", "Daily plan limits and remaining usage"),
        ("connect_exchange", "Connect a supported account read path"),
        ("disconnect_exchange", "Disconnect an exchange"), ("my_exchanges", "Connected exchanges"),
        ("exchanges", "Supported exchanges"), ("exchange_balance", "Account balance"),
        ("exchange_positions", "Exchange positions"), ("exchange_orders", "Exchange orders"),
        ("exchange_symbol", "Instrument rules"), ("exchange_account", "Account state"),
        ("exchange_safety", "Exchange safety state"), ("exchange_preflight", "Readiness preflight"),
        ("start", "Onboarding and primary menu"), ("help", "Canonical command navigation"),
        ("commands", "Search the public command catalog", "/commands orderbook"),
    ),
    "scanner": _entries(
        ("scanner", "Priority Score V3 ranking and transparent filters", "/scanner breakout"),
        ("pump_scan", "Recent broad-market pump/dump anomaly episodes"),
        ("scanner_settings", "Configure broad-market anomaly alerts", "/scanner_settings threshold 4"),
        ("signal_rankings", "Signal Ranking V5 research view"),
        ("rankings", "Compact alias for signal rankings"),
    ),
    "watchlist": _entries(
        ("watchlist", "View, rank, or edit your smart watchlist", "/watchlist add BTC SOL"),
    ),
    "alerts": _entries(
        ("alerts", "Configure notification categories", "/alerts quality on"),
    ),
    "premium": _entries(
        ("premium", "Plan overview"), ("plans", "Free, Pro, and Elite comparison"),
        ("my_plan", "Effective plan, source, and limits"),
        ("usage", "Daily plan limits and remaining usage"),
        ("capabilities", "Effective entitlement capabilities"),
    ),
    "settings": _entries(
        ("settings", "Personal output preferences", "/settings mode detailed"),
        ("language", "Persist English, Russian, Ukrainian, Hebrew, or Arabic", "/language he"),
        ("profile", "User profile and active plan"),
    ),
    "ai": _entries(
        ("ai_status", "AI advisory mode and provider identity"),
        ("ai_decision", "AI observation for a signal", "/ai_decision 434"),
        ("ai_explain", "Alias for AI decision", "/ai_explain 434"),
        ("ai_metrics", "Current-identity AI metrics"),
        ("ai_compare", "Deterministic/AI comparison"),
        ("ai_quality", "AI quality diagnostics"),
        ("ai_dashboard", "AI cost and reuse dashboard"),
        ("ai_history", "Recent AI observations"),
        ("ai_abstentions", "Current or historical abstentions", "/ai_abstentions current"),
        ("ai_failures", "Current or historical failures", "/ai_failures history"),
        ("ai_regimes", "AI outcomes by regime"), ("ai_similarity", "Similarity diagnostics"),
        ("ai_learning", "Bounded learning summary"), ("ai_statistics", "AI statistical report"),
        ("ai_counterfactual", "AI counterfactual report"),
        ("ai_provider_health", "Provider health"), ("ai_cost", "Provider cost report"),
        ("ai_research_compare", "Advisory red-team comparison"),
    ),
    "live": _entries(
        ("live_status", "Per-user LIVE state and connection health", "/live_status bingx"),
        ("live_account", "Detailed LIVE account state", "/live_account bingx"),
        ("live_sync", "Read-only account synchronization", "/live_sync bingx BTCUSDT"),
        ("live_readiness", "Fail-closed readiness reasons", "/live_readiness bingx"),
        ("live_preflight", "Alias for the complete LIVE preflight", "/live_preflight bingx"),
        ("live_certify", "Structural certification without production orders", "/live_certify bingx"),
        ("live_dry_run", "Validate adapter paths without economic orders", "/live_dry_run bingx on"),
        ("live_confirm", "Record a private two-step consent token", "/live_confirm bingx"),
        ("live_enable", "Explicit enablement after every gate passes", "/live_enable bingx"),
        ("live_disable", "Immediately block new LIVE entries", "/live_disable bingx"),
        ("live_risk", "View or replace the complete deterministic risk policy",
         "/live_risk bingx set max_positions=2 max_order=50 max_portfolio=200 max_symbol=100 max_realized_loss=20 max_total_loss=30 max_slippage_bps=20 cooldown=60 leverage=2 symbols=BTCUSDT blocked_symbols= timeframes=5m,15m strategies=SMC directions=BUY,SELL"),
        ("live_copy_settings", "Per-connection consent, filters and deterministic sizing",
         "/live_copy_settings bingx"),
        ("live_daily_pnl", "Authoritative UTC exchange PnL and fee guard", "/live_daily_pnl bingx"),
        ("live_positions", "Current exchange LIVE positions", "/live_positions bingx"),
        ("live_orders", "Current exchange LIVE orders", "/live_orders bingx"),
        ("live_performance", "Separate LIVE queue, execution and fee analytics"),
        ("live_execution", "Alias for LIVE execution analytics"),
        ("live_history", "Alias for LIVE execution history"),
        ("live_emergency_close", "Preview a user-owned reduce-only emergency close",
         "/live_emergency_close bingx"),
        ("live_emergency_confirm", "Confirm an unexpired emergency-close preview token",
         "/live_emergency_confirm TOKEN"),
        ("live_reconciliation", "Exchange-vs-local mismatch check", "/live_reconciliation bingx"),
        ("recovery", "Unresolved LIVE execution states", "/recovery bingx"),
        ("demo_order", "Explicit BingX demo-account order", "/demo_order bingx BTCUSDT BUY MARKET 0.001 60000 3"),
        ("demo_cancel", "Cancel a demo-account order", "/demo_cancel bingx BTCUSDT ORDER_ID"),
        ("demo_status", "Inspect a demo-account order", "/demo_status bingx BTCUSDT ORDER_ID"),
        ("demo_kill", "Disable demo execution for this runtime"),
        ("demo_resume", "Release the runtime demo switch"),
    ),
}


OPERATOR_COMMANDS = frozenset({
    "admin_status", "migration_status", "workers", "grant_plan", "revoke_plan",
    "ai_mode", "ai_disable", "ai_provider", "ai_certification", "ai_drift",
    "ai_experiments", "ai_kill",
    "admin", "admin_plan", "admin_plan_status", "admin_plan_revoke", "admin_plan_extend",
    "admin_entitlements", "admin_users", "admin_usage", "admin_plans", "admin_ai_usage",
    "admin_health", "admin_worker_status",
    "scanner_health",
})

OPERATOR_HELP = (
    "Operator-only: /admin_status /migration_status /workers /scanner_health /grant_plan /revoke_plan\n"
    "AI governance: /ai_mode /ai_disable /ai_provider /ai_certification /ai_drift /ai_experiments /ai_kill\n"
    "User-scoped LIVE and demo controls are documented under /help live."
)

PUBLIC_COMMANDS = frozenset(entry.command for entries in HELP_CATALOG.values() for entry in entries)
ALL_DOCUMENTED_COMMANDS = PUBLIC_COMMANDS | OPERATOR_COMMANDS


class CommandClass(StrEnum):
    PUBLIC = "PUBLIC"
    PREMIUM_PUBLIC = "PREMIUM_PUBLIC"
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    INTERNAL = "INTERNAL"
    DEPRECATED_ALIAS = "DEPRECATED_ALIAS"


PREMIUM_PUBLIC_COMMANDS = frozenset({
    "orderbook", "funding", "open_interest", "copy_analytics", "ai_decision",
    "ai_explain", "ai_dashboard", "ai_research_compare",
})
COMMAND_CLASSIFICATION = {
    **{command: (CommandClass.PREMIUM_PUBLIC if command in PREMIUM_PUBLIC_COMMANDS else CommandClass.PUBLIC)
       for command in PUBLIC_COMMANDS},
    **{command: CommandClass.OPERATOR for command in OPERATOR_COMMANDS},
    "strategy_compare": CommandClass.DEPRECATED_ALIAS,
    "ai_explain": CommandClass.DEPRECATED_ALIAS,
}

MAIN_MENU_COMMANDS = (
    ("start", "Open Liquidity Vision"), ("help", "Browse intelligence commands"),
    ("analyze", "Analyze a market"),
    ("scanner", "Rank opportunities"), ("watchlist", "Your tracked markets"),
    ("trade", "Trade replay"), ("journal", "Trade journal"),
    ("copy", "PAPER copy overview"), ("positions", "PAPER positions"),
    ("rankings", "Ranked signals"), ("research", "Research hub"),
    ("settings", "Personal settings"), ("alerts", "Alert preferences"),
    ("premium", "Plans and capabilities"), ("profile", "Profile and plan"),
)


@dataclass(frozen=True)
class FunctionMetadata:
    """Authoritative user-facing route metadata.

    HELP_CATALOG remains a compact declaration syntax above; all product
    consumers and audits use this normalized registry.
    """
    id: str
    command: str | None
    title: str
    description: str
    category: str
    permissions: str
    visibility: str
    menu_visibility: bool
    help_visibility: bool
    admin_only: bool = False
    experimental: bool = False
    callback_entry: str | None = None
    deprecated: bool = False
    usage: str | None = None


_MENU_COMMANDS = frozenset(name for name, _ in MAIN_MENU_COMMANDS)
_PRIMARY_CATEGORY = {
    "scanner": "scanner", "signal_rankings": "scanner", "rankings": "scanner",
    "watchlist": "watchlist", "alerts": "alerts", "premium": "premium",
    "plans": "premium", "my_plan": "premium", "usage": "premium",
    "settings": "settings", "language": "settings",
}
_CALLBACK_FUNCTIONS = (
    ("callback.analyze", "Analyze quick action", "market", "analyze_*"),
    ("callback.deep_analyze", "Deep Analyze quick action", "market", "deep:*"),
    ("callback.orderflow", "Order Flow quick action", "market", "flow:*"),
    ("callback.watchlist_add", "Add to watchlist", "watchlist", "watch:*"),
    ("callback.pump_mute", "Mute scanner symbol", "scanner", "pdmute:*"),
    ("callback.scanner_settings", "Scanner settings", "scanner", "pdsettings"),
    ("callback.refresh", "Refresh analysis", "market", "refresh:*"),
    ("callback.explain", "Explain analysis", "market", "explain:*"),
    ("callback.why_not", "Why not trade", "market", "whynot:*"),
    ("callback.technical", "Technical details", "market", "technical:*"),
    ("callback.scenarios", "Scenario view", "market", "scenarios:*"),
    ("callback.history", "Signal history", "trading", "history:*"),
    ("callback.similar", "Similar setups", "research", "similar:*"),
)


def _build_registry() -> tuple[FunctionMetadata, ...]:
    rows: list[FunctionMetadata] = []
    seen: set[str] = set()
    for category, entries in HELP_CATALOG.items():
        for entry in entries:
            if entry.command in seen:
                continue
            seen.add(entry.command)
            classification = COMMAND_CLASSIFICATION.get(entry.command, CommandClass.PUBLIC)
            research = category == "research"
            deprecated = classification == CommandClass.DEPRECATED_ALIAS
            permissions = ("PREMIUM" if classification == CommandClass.PREMIUM_PUBLIC
                           else "RESEARCH" if research else "PUBLIC")
            rows.append(FunctionMetadata(
                id=f"command.{entry.command}", command=entry.command,
                title=entry.command.replace("_", " ").title(), description=entry.summary,
                category=_PRIMARY_CATEGORY.get(entry.command, category), permissions=permissions,
                visibility="DEPRECATED" if deprecated else "PUBLIC",
                menu_visibility=entry.command in _MENU_COMMANDS,
                help_visibility=True, experimental=category in {"research", "ai", "live"},
                deprecated=deprecated, usage=entry.usage,
            ))
    for command in sorted(OPERATOR_COMMANDS):
        rows.append(FunctionMetadata(
            id=f"command.{command}", command=command,
            title=command.replace("_", " ").title(), description="Operator control",
            category="admin", permissions="OPERATOR", visibility="HIDDEN",
            menu_visibility=False, help_visibility=False, admin_only=True,
        ))
    for identity, title, category, callback in _CALLBACK_FUNCTIONS:
        rows.append(FunctionMetadata(
            id=identity, command=None, title=title, description=title,
            category=category, permissions="PUBLIC", visibility="PUBLIC",
            menu_visibility=True, help_visibility=False, callback_entry=callback,
        ))
    return tuple(rows)


FUNCTION_REGISTRY = _build_registry()
FUNCTION_BY_COMMAND = {item.command: item for item in FUNCTION_REGISTRY if item.command}


def registry_counts() -> dict[str, int]:
    return {
        "TOTAL_FUNCTIONS": len(FUNCTION_REGISTRY),
        "PUBLIC": sum(item.visibility == "PUBLIC" and item.permissions not in {"OPERATOR", "RESEARCH"}
                      for item in FUNCTION_REGISTRY),
        "ADMIN": sum(item.admin_only for item in FUNCTION_REGISTRY),
        "RESEARCH": sum(item.permissions == "RESEARCH" for item in FUNCTION_REGISTRY),
        "HIDDEN": sum(item.visibility == "HIDDEN" for item in FUNCTION_REGISTRY),
        "DEPRECATED": sum(item.deprecated for item in FUNCTION_REGISTRY),
        "UNREACHABLE": 0,
    }


def audit_handler_commands(handler_root: str) -> dict[str, tuple[str, ...]]:
    """AST audit used by tests and release checks; avoids regex-only discovery."""
    import ast
    from pathlib import Path
    discovered: set[str] = set()
    for path in Path(handler_root).glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "Command"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    discovered.add(arg.value)
    documented = {item.command for item in FUNCTION_REGISTRY if item.command}
    return {
        "discovered": tuple(sorted(discovered)),
        "documented": tuple(sorted(documented)),
        "unreachable": tuple(sorted(discovered - documented)),
        "documented_without_handler": tuple(sorted(documented - discovered)),
    }


def help_functions(category: str | None = None) -> tuple[FunctionMetadata, ...]:
    return tuple(item for item in FUNCTION_REGISTRY
                 if item.help_visibility and item.command
                 and (category is None or item.category == category))


def category_text(category: str, language: str = "en") -> str | None:
    from services.localization import LocalizationService
    entries = help_functions(category)
    if not entries:
        return None
    i18n = LocalizationService()
    lines = [f"<b>{category.title()} · {i18n.t('help.title', language=language)}</b>",
             i18n.t(f"help.{category}", language=language), ""]
    if category == "live":
        lines.extend([
            i18n.t("help.live.lifecycle", language=language),
            i18n.t("help.live.boundary", language=language),
            i18n.t("live.risk_warning", language=language),
            "",
        ])
    for entry in entries:
        command = i18n.market_token(f"/{entry.command}", language=language)
        usage = i18n.market_token(entry.usage or "/" + entry.command, language=language)
        lines.append(f"<b>{command}</b> — {entry.description}")
        lines.append(f"  {i18n.t('common.usage', language=language)}: <code>{usage}</code>")
    return "\n".join(lines)

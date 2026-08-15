from __future__ import annotations

from dataclasses import dataclass


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
        ("price", "Current public market price", "/price BTC"),
        ("scanner", "Rank the bounded opportunity universe", "/scanner breakout"),
        ("market", "Compact market overview"), ("news", "Market news feed"),
        ("fear", "Fear and Greed context"),
        ("market_story", "Decision-time market narrative", "/market_story 434"),
        ("signal_quality", "Signal Quality V3", "/signal_quality 434"),
        ("liquidity_map", "Unresolved and consumed liquidity", "/liquidity_map BTCUSDT"),
        ("orderbook", "Bounded order-book intelligence", "/orderbook BTCUSDT"),
        ("funding", "Public funding snapshot", "/funding BTCUSDT"),
        ("open_interest", "Public open-interest snapshot", "/open_interest BTCUSDT"),
        ("data_health", "Data availability and remediation", "/data_health BTCUSDT"),
        ("pump_reversals", "Pump reversal research feed"),
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
        ("copy_guard", "Safety guard state"), ("copy_training", "Training summary"),
        ("copy_rejections", "Admission rejection analytics"),
        ("copy_guardrails", "Effective copy guardrails"),
        ("copy_similar", "Similar historical copy cases"), ("genome", "Copy policy genome"),
        ("copy_queue", "Durable queue state"), ("copy_plan", "Decision-to-order plan"),
        ("orders", "PAPER orders"), ("execution", "Execution events"),
        ("fills", "PAPER fills"), ("panic", "Fail-closed copy stop"),
    ),
    "intelligence": _entries(
        ("signal_rankings", "Signal Ranking V4"), ("contradictions", "Supports and conflicts", "/contradictions 434"),
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
    ),
    "system": _entries(
        ("system_health", "System Health V2"),
    ),
    "account": _entries(
        ("profile", "User profile"), ("premium", "Plan overview"),
        ("plans", "Free, Pro, and Elite comparison"), ("my_plan", "Effective plan and limits"),
        ("settings", "Personal output preferences"), ("alerts", "Notification preferences"),
        ("connect_exchange", "Connect a supported account read path"),
        ("disconnect_exchange", "Disconnect an exchange"), ("my_exchanges", "Connected exchanges"),
        ("exchanges", "Supported exchanges"), ("exchange_balance", "Account balance"),
        ("exchange_positions", "Exchange positions"), ("exchange_orders", "Exchange orders"),
        ("exchange_symbol", "Instrument rules"), ("exchange_account", "Account state"),
        ("exchange_safety", "Exchange safety state"), ("exchange_preflight", "Readiness preflight"),
        ("start", "Onboarding and primary menu"), ("help", "Canonical command navigation"),
    ),
}


OPERATOR_COMMANDS = frozenset({
    "admin_status", "migration_status", "workers", "grant_plan", "revoke_plan",
    "ai_mode", "ai_disable", "ai_provider", "ai_certification", "ai_drift",
    "ai_experiments", "ai_kill", "live_sync", "live_certify", "live_account",
    "live_dry_run", "live_confirm", "live_disable", "live_readiness", "recovery",
    "demo_order", "demo_cancel", "demo_status", "demo_kill", "demo_resume",
})

OPERATOR_HELP = (
    "Operator-only: /admin_status /migration_status /workers /grant_plan /revoke_plan\n"
    "AI governance: /ai_mode /ai_disable /ai_provider /ai_certification /ai_drift /ai_experiments /ai_kill\n"
    "LIVE/demo governance: /live_readiness /live_certify /live_dry_run /live_confirm /live_disable /recovery "
    "/demo_status /demo_kill /demo_resume /demo_order /demo_cancel"
)

PUBLIC_COMMANDS = frozenset(entry.command for entries in HELP_CATALOG.values() for entry in entries)
ALL_DOCUMENTED_COMMANDS = PUBLIC_COMMANDS | OPERATOR_COMMANDS

MAIN_MENU_COMMANDS = (
    ("help", "Browse intelligence commands"), ("analyze", "Analyze a market"),
    ("scanner", "Rank opportunities"), ("watchlist", "Your tracked markets"),
    ("trade", "Trade replay and journal"), ("copy", "PAPER copy overview"),
    ("positions", "PAPER positions"), ("signal_rankings", "Ranked signals"),
    ("research", "Research hub"), ("profile", "Profile and plan"),
    ("premium", "Plans and capabilities"),
)


def category_text(category: str) -> str | None:
    entries = HELP_CATALOG.get(category)
    if entries is None:
        return None
    lines = [f"<b>{category.title()} · Command Catalog</b>", ""]
    for entry in entries:
        lines.append(f"/<b>{entry.command}</b> — {entry.summary}")
        if entry.usage:
            lines.append(f"  Example: <code>{entry.usage}</code>")
    return "\n".join(lines)

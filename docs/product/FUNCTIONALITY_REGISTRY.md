# Liquidity Vision functionality registry

This document describes the normalized registry implemented in
`services/command_catalog.py`. The registry is the product authority for slash
commands, help visibility, menu visibility, permission class, experimental
status, aliases and callback entry points. `HELP_CATALOG` is retained only as
the compact declaration syntax from which `FUNCTION_REGISTRY` is built.

The release audit parses every `Command(...)` decorator with Python's AST and
compares it with the registry. The current result is:

| Measure | Count |
|---|---:|
| Total registered functions | 189 |
| Public product functions and callbacks | 145 |
| Operator/admin functions | 24 |
| Research functions | 19 |
| Intentionally hidden functions | 24 |
| Deprecated aliases | 2 |
| Unreachable command handlers | 0 |
| Documented commands without a handler | 0 |

`ADMIN`, `HIDDEN`, `RESEARCH`, and `DEPRECATED` are metadata dimensions and can
overlap. No dangerous operator command is exposed in the public menu or public
help index. Research commands remain explicitly marked experimental. LIVE
commands describe the existing per-user readiness controls, but global LIVE
execution remains disabled and fail-closed.

Every normalized record declares `id`, `command`, `title`, `description`,
`category`, `permissions`, `visibility`, `menu_visibility`, `help_visibility`,
`admin_only`, `experimental`, `callback_entry`, `deprecated`, and `usage`.

## Public route inventory

### Market and analysis

`/analyze`, `/deep_analyze`, `/price`, `/market`, `/news`, `/fear`,
`/market_story`, `/signal_quality`, `/liquidity_map`, `/orderbook`, `/funding`,
`/open_interest`, `/data_health`, `/market_now`, `/orderflow`,
`/pump_reversals`, `/terminal`.

Quick actions: `analyze_*`, `deep:*`, `flow:*`, `refresh:*`, `explain:*`,
`whynot:*`, `technical:*`, and `scenarios:*`.

### Scanner, alerts, and watchlist

`/scanner`, `/signal_rankings`, `/rankings`, `/pump_scan`,
`/scanner_settings`, `/watchlist`, `/alerts`.

Quick actions: `pdmute:*`, `pdsettings`, and `watch:*`.

### Trading and portfolio

`/journal`, `/trade`, `/closeall`, `/positions`, `/performance`, `/portfolio`,
`/dna`, `/insights`, `/cancel`. Replay history is reachable through
`history:*`.

### PAPER copy product

`/copy`, `/copy_profile`, `/copy_symbols`, `/copy_filters`, `/copy_enable`,
`/copy_disable`, `/copy_risk`, `/copy_size`, `/copy_leverage`, `/copy_auto`,
`/copy_balance`, `/copy_limits`, `/copy_stats`, `/copy_diagnostics`,
`/copy_performance`, `/copy_analytics`, `/copy_guard`, `/copy_training`,
`/copy_rejections`, `/copy_guardrails`, `/copy_similar`, `/genome`,
`/copy_queue`, `/copy_plan`, `/orders`, `/execution`, `/fills`, `/panic`.

These are PAPER routes. They do not authorize real orders.

### Intelligence and advisory AI

`/contradictions`, `/regimes`, `/ai_status`, `/ai_decision`, `/ai_explain`,
`/ai_metrics`, `/ai_compare`, `/ai_quality`, `/ai_dashboard`, `/ai_history`,
`/ai_abstentions`, `/ai_failures`, `/ai_regimes`, `/ai_similarity`,
`/ai_learning`, `/ai_statistics`, `/ai_counterfactual`,
`/ai_provider_health`, `/ai_cost`, `/capabilities`.

### Account, system, and product

`/start`, `/help`, `/commands`, `/profile`, `/premium`, `/plans`, `/my_plan`,
`/usage`, `/settings`, `/language`, `/system_health`, `/terminal_health`,
`/shadow_status`, `/connect_exchange`, `/disconnect_exchange`,
`/my_exchanges`, `/exchanges`, `/exchange_balance`, `/exchange_positions`,
`/exchange_orders`, `/exchange_symbol`, `/exchange_account`,
`/exchange_safety`, `/exchange_preflight`.

## Research routes

`/research`, `/strategy_lab`, `/strategy_compare`, `/edge_report`,
`/edge_discovery`, `/feature_edge`, `/hypotheses`, `/forward_tests`,
`/rr_research`, `/exit_research`, `/confidence_research`, `/portfolio_edge`,
`/scalping_research`, `/ai_research_compare`, `/entry_research`,
`/reentry_research`, `/quality_report`, `/quality_cohorts`,
`/strategy_distribution`, `/export_analytics`, plus `similar:*`.

`/strategy_compare` and `/ai_explain` are retained as deprecated aliases.

## LIVE and demo routes

The existing readiness and user-consent surface remains registered:

`/live_status`, `/live_account`, `/live_sync`, `/live_readiness`,
`/live_preflight`, `/live_certify`, `/live_dry_run`, `/live_confirm`,
`/live_enable`, `/live_disable`, `/live_risk`, `/live_copy_settings`,
`/live_daily_pnl`, `/live_positions`, `/live_orders`, `/live_performance`,
`/live_execution`, `/live_history`, `/live_emergency_close`,
`/live_emergency_confirm`, `/live_reconciliation`, `/recovery`, `/demo_order`,
`/demo_cancel`, `/demo_status`, `/demo_kill`, `/demo_resume`.

These routes do not defeat the four global fail-closed switches. They are not
shown in the first-level menu.

## Intentionally hidden operator routes

`/admin`, `/admin_ai_usage`, `/admin_entitlements`, `/admin_health`,
`/admin_plan`, `/admin_plan_extend`, `/admin_plan_revoke`,
`/admin_plan_status`, `/admin_plans`, `/admin_status`, `/admin_usage`,
`/admin_users`, `/admin_worker_status`, `/ai_certification`, `/ai_disable`,
`/ai_drift`, `/ai_experiments`, `/ai_kill`, `/ai_mode`, `/ai_provider`,
`/grant_plan`, `/migration_status`, `/revoke_plan`, `/workers`.

They require operator authorization and appear only through the authorized
operator help path.

## First-level reply menu

The bounded first-level menu is Markets, Analyze, Scanner, Signals, Order Flow,
Paper, Shadow Lab, Portfolio, Risk, Alerts, Settings, System, and Open Terminal.
Legacy slash commands and intentionally retained callback paths continue to
work even though they are not all shown as first-level reply buttons.

## Product authority boundaries

- Pump/dump and auxiliary anomaly outputs are `MARKET_ALERT` records and have
  `economic_authority=false`.
- Deep Analyze displays the authoritative DecisionQuality action and a
  separate shadow confirmation card.
- `TradeConfirmationEngine` sets `production_gate=false` and cannot mutate or
  replace the source decision.
- The Mini App uses only bounded PostgreSQL current state and aggregate rows.
- Frozen forward candidates and their evidence-start semantics are not
  imported by these product components.

## Executable runtime matrix

`services.functionality_audit.full_functionality_matrix()` expands this
registry into the release-time ownership audit. The current matrix contains
256 unique rows: all 189 normalized commands/callbacks, 13 reply controls, 13
Terminal pages, 17 scanner discovery modes, 14 configurable scanner alert
categories, and 10 continuous/maintenance/migration components. Each row
records its concrete handler, service dependencies, data source, state owner,
background dependency, tables, output contract, failure/degradation path,
runtime class, runtime owner, permissions and economic authority.

The audit resolves callback decorators from source rather than treating router
registration as proof of functionality. Release tests require every callback
to resolve to a concrete handler, every visible reply control to map to a real
command, every Terminal page to have an authenticated API route, and every
matrix ID to be unique.

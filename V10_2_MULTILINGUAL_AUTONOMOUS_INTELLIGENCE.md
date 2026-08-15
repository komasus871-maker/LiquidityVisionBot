# LiquidityVisionBot v10.2.0

## Release contract

v10.2.0 is a product-intelligence release. It adds no economic or exchange
execution authority. Automatic copy remains PAPER. LIVE remains independently
certified and fail-closed. AI remains advisory, cost-bounded and unable to
approve, size, route, or submit an order.

## Operator authorization

Telegram user `7975010097` is the immutable application owner. The owner has
operator capabilities but is not assigned Pro or Elite automatically. Plan
changes remain explicit commands and write both entitlement and operator audit
events with previous/new state. Additional operators may be configured using:

- `TELEGRAM_PLAN_ADMIN_USER_IDS`
- `TELEGRAM_SYSTEM_ADMIN_USER_IDS`
- `TELEGRAM_RESEARCH_ADMIN_USER_IDS`
- `TELEGRAM_AI_ADMIN_USER_IDS`
- `TELEGRAM_OPERATOR_USER_IDS` (all operator roles)

`ADMIN_IDS`/`ADMIN_ID` are temporary compatible all-role aliases. Invalid or
unknown roles fail closed. Use `/admin`, `/admin_health`, `/admin_usage`, and
`/admin_ai_usage` for private operational views.

## Product and language

`/language` persists `en`, `ru`, `uk`, `he`, or `ar`. Telegram command menus,
onboarding, categorized help, plan/settings/usage, scanner, watchlist and
public health surfaces use the preference with deterministic English fallback.
Hebrew and Arabic isolate symbols, timeframes, directions, prices and ages so
mixed-direction text remains readable.

Free provides useful core analysis/PAPER features. Pro adds full intelligence,
alerts and customization. Elite adds research, custom scanner filters, AI
red-team advisory and detailed cohort analytics. `/usage` shows daily limits;
entitlements never change deterministic risk or execution gates.

## Intelligence upgrades

- Scanner V2 presents Scanner Score, Signal Quality, Entry Readiness and
  strategy suitability as separate diagnostics.
- Entry Readiness V2 exposes location, trigger, momentum, microstructure,
  invalidation, after-cost reward and data-confidence components.
- Strategy Fusion V2 records primary/secondary strategy, suitability gap and
  ambiguity state.
- Market Regime V2 and Momentum Reacceleration classify expansion,
  compression/rotation, exhaustion and evidence-backed explosive-continuation
  candidates.
- Microstructure V2 separates interaction, persistence, spread and depth
  stability. BTC benchmark V2 adds correlation/beta states. Funding/OI V2
  reports trend/acceleration only when sufficient history exists.

All scores explicitly remain non-probabilistic research diagnostics.

## Alerts, replay and PAPER research

Watch Engine now routes material changes through Alert Engine V3. Preference
category, entitlement, daily usage, unchanged-state and debounce checks occur
before delivery; delivery state is persisted. Smart Watchlist V2 ranks by
Quality, Readiness and strategy fit, reports freshness, and never exposes raw
provider errors.

Trade Replay separates immutable decision-time intelligence from later outcome
evolution. PAPER Copy Analytics V2 groups resolved executed and zero-exposure
shadow outcomes by strategy, timeframe, symbol, decision-time Quality and
Entry Readiness. MAX_SLIPPAGE, MAX_HEAT and LOW_CONFIDENCE counterfactuals
report losses avoided and wins missed, but never alter policy automatically.

## Release validation

Run focused v10.2 tests during implementation, then one stabilized full suite,
compileall, import smoke, two consecutive schema initializations, diff/conflict/
secret scans, and execution-authority/operator-security scans. No deployment,
commit, push, paid OpenAI request, authenticated exchange order or LIVE action
is part of this release procedure.

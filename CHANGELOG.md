## v9.9.9 — Provenance Migration and Unified Read Cutover

- Added a restart-safe, bounded historical execution catalog with explicit fully reconstructable, partially reconstructable, legacy-only, ambiguous, and invalid classifications.
- Added stable source keys, checksums, provenance metadata, migration runs, coverage diagnostics, and an admin migration-status command.
- Cut adaptive training reads over to a normalized repository that prioritizes unified outcomes, prevents linked legacy duplicates, and retains an explicit compatibility path while coverage is incomplete.
- Preserved all legacy tables and prohibited synthetic orders, fills, commissions, lifecycle events, and portfolio ledger entries during migration.
- Included the PostgreSQL-safe parity aggregate fix with unique aliases and backend-neutral row access.

## v9.9.8 — Unified Portfolio Accounting

- Added one normalized unified portfolio read model for open state, remaining-risk heat, realized/unrealized PnL, fees, equity, exposure, daily loss, cooldown, realized R, and rejections.
- Added an idempotent append-only portfolio ledger and lifecycle commission deltas.
- Added complete/partial/missing/invalid risk classification and fail-closed unified admission.
- Added `LEGACY`/`SHADOW`/`UNIFIED` rollout configuration, parity diagnostics, and unified Telegram accounting labels.
- Preserved legacy tables and historical analytics compatibility for the v9.9.9 backfill.

## 9.9.7 — Unified Lifecycle Authority

- Made `paper_execution_positions` authoritative for new paper position lifecycle mutations.
- Routed automatic copy opens through `CopyExecutionEngine` instead of duplicating journal, claim, order and fill orchestration.
- Added idempotent signal lifecycle commands for mark, partial close, terminal close and panic close.
- Added a durable position-lifecycle event ledger with exactly-once event keys.
- Changed legacy `paper_positions` updates into a rollback-compatible projection of unified positions.
- Prevented reconciliation from independently closing a legacy projection while its unified position remains open.
- Added lifecycle integrity diagnostics for duplicate, invalid-open and closed-with-quantity states.
- Preserved legacy-only lifecycle fallback for pre-unified historical positions.
- Made compatibility event projection crash-replayable and idempotent with durable source keys.
- Enforced quantity/fraction parity for partial fills and rejected out-of-order lifecycle regressions.
- Made manual unified closes lifecycle-event backed and panic retries repair stale projections.

## 9.9.6.9 — Unified Risk-Aware Portfolio

- Persisted the planned stop, initial quantity, and initial risk on unified paper executions.
- Added remaining-risk heat for unified-only positions, including partial closes.
- Kept exact `signal_id` legacy/unified matches deduplicated for both position count and heat.
- Kept pre-release unified positions without durable risk metadata diagnostics-only for backward compatibility.
- Added unified and hybrid risk diagnostics without making unified PnL authoritative for sizing or equity.
- Preserved paper-only execution, idempotency, and the no-synthetic-lifecycle-data contract.

## 9.9.6.8 — Hybrid Position Awareness

- Added an aggregated read model for real open unified execution positions.
- Runtime portfolio identity now combines confirmed legacy and unified positions with signal-id deduplication.
- Unified-only open symbols now participate in existing duplicate-symbol and maximum-position guardrails.
- Portfolio heat, equity, sizing, cooldown, and daily realized PnL retain their existing legacy semantics.
- Added compact Telegram diagnostics for hybrid counts and unified exposure/PnL read models.
- No database schema changes and no synthetic orders, fills, prices, or positions.

# v9.9.6.7 — Safe Heat Reconciliation

- Classified legacy open positions as confirmed active, terminal stale, or unresolved.
- Removed terminal stale rows from confirmed portfolio heat idempotently.
- Added fail-closed `PORTFOLIO_STATE_UNRESOLVED` rejection for missing or unknown legacy signal state.
- Preserved `MAX_HEAT` for confirmed active portfolio risk.
- Added heat-source and reconciliation diagnostics to `/copy_stats` and `/positions`.
- Added targeted reconciliation, validator, idempotency, empty-state, and formatter tests.

# v9.9.6.6 — Legacy Portfolio Reconciliation

- Added conservative, idempotent reconciliation of terminal legacy paper positions.
- Removed stale terminal legacy rows from open-position count and portfolio heat without fabricating fills.
- Added legacy/unified mismatch diagnostics to copy statistics and positions UI.
- Added targeted tests for stale `MAX_HEAT`, idempotency, and fail-closed active legacy state.

# v9.9.6.3 — Unified Execution Foundation Builds 1–3

- Added immutable `ExecutionContext` pipeline carrier.
- Added centralized unified pipeline state validation.
- Added context-aware `ExecutionDispatcher` with legacy adapter compatibility.
- Integrated the new foundation into `CopyExecutionEngine`.
- Added context entry points to the paper lifecycle and paper adapter.
- Kept LIVE execution fail-closed and preserved existing database schemas.

# v9.9.6 — Recovery & Reliability Engine

- Durable worker claims with identity, token, timestamps, and expiring leases.
- Persistent RETRY_WAIT and DEAD_LETTER execution states.
- Exponential retry backoff for transient adapter failures.
- Automatic recovery of stale EXECUTING claims after worker restarts.
- Maximum-attempt protection and dead-letter isolation.
- Copy worker heartbeat and recovery counters in runtime diagnostics.
- Admin visibility for claimed, retrying, and dead-letter executions.

# Changelog

## v9.9.5g — Unified Paper Execution Lifecycle

- Added persistent paper orders, fills, positions, and order transition events.
- Added guarded order state transitions and terminal-state protection.
- Added idempotent full and partial fill recording with average price, commission, and slippage.
- Integrated both CopyExecutionEngine and the production copy-trading path.
- Upgraded `/orders`, `/execution`, and `/fills`; added `/positions`.
- Added lifecycle, persistence, partial-fill, and replay tests.

# v9.9.5f — Execution Observability & Transition Audit

- Added persistent actor-aware execution transition history.
- Added user-scoped `/orders`, `/execution`, and `/fills` Telegram inspection commands.
- Added execution lookup by order ID, signal ID, plan ID, idempotency key, and execution reference.
- Added read-only inspection service and lifecycle audit tests.
- Preserved Planner/Queue/Engine compatibility and fail-closed LIVE behavior.

# 9.9.5e — Planner → Engine Queue Integration

- Added `ExecutionQueueService` as the durable Planner → Engine hand-off.
- Reused `PLANNED` execution-journal rows as the persistent FIFO queue instead of creating duplicate storage.
- Added immutable plan reconstruction, idempotent enqueue, queue drain, and per-user status summaries.
- Routed `CopyTradingService.plan_execution()` through the queue boundary.
- Added `/copy_plan` and `/copy_queue` Telegram diagnostics.
- Preserved PAPER-only execution and future recovery compatibility.

# 9.9.5d — Execution Validation Pipeline

- Added a composable, fail-closed validation boundary between `CopyExecutionPlanner` and `CopyExecutionEngine`.
- Added plan approval, identity, order-payload and PAPER-safety validators with structured failure results.
- Integrated validation before journal claim so invalid plans are persisted as `REJECTED` and never reach an adapter.
- Preserved existing signal, sizing and portfolio validation ownership inside the Planner layer.
- Added dependency injection for future exchange/risk validators and regression coverage for invalid plans and LIVE blocking.
- Removed fragile current-version assertions from historical regression tests so later releases do not invalidate earlier subsystem coverage.

# 9.9.5c — Journal State Machine Integration

- Added a centralized, explicit lifecycle transition table for the persistent copy execution journal.
- Enforced legal state transitions at the journal persistence boundary instead of relying on callers.
- Added terminal-state protection, idempotent same-state updates, and compare-and-set persistence guards.
- Preserved the existing `CopyExecutionEngine`, adapter contract, database schema, and public journal statuses.
- Added focused regression tests for valid execution flow, illegal transitions, terminal immutability, and metadata preservation.

# 9.9.5a — Paper Execution Engine Foundation

- Added `CopyExecutionEngine` as the idempotent Planner → Journal → Adapter coordinator.
- Added an explicit execution-adapter contract and deterministic `PaperExecutionAdapter`.
- Added atomic journal claiming, terminal execution persistence, duplicate replay protection, and adapter-failure capture.
- Kept LIVE execution fail-closed by contract.
- Added focused regression coverage for successful execution, rejection persistence, idempotency, adapter exceptions, and LIVE blocking.

# v9.9.4 — Execution Journal & Idempotency Foundation

- Added persistent copy execution journal with unique idempotency reservations.
- Added lifecycle states, attempt tracking, execution references and failure storage.
- Integrated journal reservations and transitions into the existing paper-copy path.
- LIVE exchange execution remains disabled.

# Changelog

## 9.9.3 — Copy Execution Planning Layer

- Added a deterministic, side-effect-free `CopyExecutionPlanner`; v9.9.16 now uses this contract for automatic PAPER execution.
- Plans now carry sizing, leverage, entry, SL/TP, risk, profile snapshot, validation outcome, and stable idempotency metadata.
- Integrated existing paper-copy opening flow with the planner while preserving current behavior and guardrails.
- Added fail-closed `AUTO_COPY_DISABLED` support, retained by the automatic PAPER execution path completed in v9.9.16.
- Added v9.9.3 regression tests and release documentation; no LIVE order execution was enabled.

## 9.9.2 — Copy Trading Profile Foundation

- Extended the existing per-user `copy_profiles` model with sizing mode, Fixed USDT, leverage, and Auto Copy preference.
- Added centralized fail-closed profile validation and safe migration defaults.
- Integrated Fixed USDT sizing into the existing Risk/Execution Validator instead of creating a parallel executor.
- Added `/copy_size`, `/copy_leverage`, and `/copy_auto` commands and updated `/copy` status output.
- Preserved all portfolio heat, daily-loss, confidence, slippage, cooldown, duplicate-symbol, and notional guardrails.
- Added v9.9.2 regression tests and release documentation. LIVE execution remains fail-closed.

## 9.9.1 — BingX Hedge Execution Hotfix

- Fixed BingX error 109400 in hedge mode by omitting `reduceOnly` from normal opening orders.
- Preserved explicit `reduceOnly=true` support for future close/reduce flows.
- Clarified `/demo_order` argument validation and retained per-user encrypted execution credentials.

## 9.9.0 — Multi-User Exchange Accounts

- Hotfixed BingX error 100001 by signing parameters in the exact order sent on the wire.
- Clarified that the BingX adapter targets USDT-M perpetual/swap permissions, not spot-only permissions.

- Added encrypted per-user exchange credential storage keyed by Telegram user ID and exchange.
- Added `/connect_exchange`, `/disconnect_exchange`, and `/my_exchanges`.
- Routed authenticated balances, positions, orders, account snapshots, preflight portfolio state, and BingX demo execution through the sender's own credentials.
- Added immediate deletion of credential-bearing Telegram messages and private-chat-only connection flow.
- Added authentication validation with rollback of invalid credentials.
- Added live-connection lock, encrypted OKX passphrase support, database schema, deployment variables, tests, and release documentation.
- Removed the shared owner-account assumption from user execution commands.

# v9.8.8 — Autonomous Demo Execution Core

- Added automatic BingX demo MARKET/LIMIT order submission without manual confirmation.
- Added demo cancellation and status synchronization.
- Added execution serialization, deterministic idempotency keys, audit JSONL, runtime kill switch and circuit breaker.
- Preserved the v9.8.7 fail-closed safety validator and hard rejection of non-demo credentials.
- Live execution remains unavailable.

# Changelog

## 9.8.7 — Authenticated Safety Core

- Added `ExchangeManager` for fail-closed authenticated account snapshots across balances, positions, and open orders.
- Added `/exchange_account` with explicit demo/live account reporting and no write access.
- Added a pure execution preflight validator with notional, leverage, whitelist, tick/step, minimum-size, portfolio-capacity, and duplicate-order checks.
- Added `/exchange_safety` and `/exchange_preflight` diagnostics.
- Added an explicit global LIVE lock; order submission remains structurally unavailable in every adapter.
- Added v9.8.7 regression coverage and release documentation.

## 9.8.5 — BingX Read-Only Reachability

- Added a read-only BingX USDT-M perpetual adapter for health, contract rules, balances, positions, and open orders.
- Added Render reachability diagnostics through BingX public server time.
- Added `BTCUSDT` → `BTC-USDT` normalization and cached public contract rules.
- Reused bounded retries, split timeouts, safe non-JSON diagnostics, and fail-fast authentication handling.
- Added BingX environment configuration while keeping credentials environment-only.
- Preserved the strict read-only contract: no order placement, modification, or cancellation methods exist.
- Added v9.8.5 regression coverage and release documentation.

## 9.8.4 — Resilient Exchange Transport

- Added bounded exponential-backoff retries for OKX timeouts, transient transport failures, rate limits, malformed responses, and HTTP 5xx failures.
- Added separate connect and read timeout configuration for more reliable Render networking.
- Added typed timeout, rate-limit, and response errors while preserving the normalized ExchangeError contract.
- Added a short-lived process cache for public OKX symbol rules to reduce redundant exchange requests.
- Kept authentication/configuration failures fail-fast and preserved the strict read-only exchange contract.
- Added v9.8.4 regression coverage and deployment configuration documentation.

## 9.8.3 — Exchange Reachability: OKX Read-Only

- Added a read-only OKX V5 adapter for public health, swap instrument rules, balances, positions, and pending orders.
- Added OKX Demo Trading support through `x-simulated-trading: 1`; API credentials remain environment-only.
- Added automatic `BTCUSDT` → `BTC-USDT-SWAP` normalization while accepting native OKX instrument IDs.
- Added robust non-JSON/HTML response diagnostics with HTTP status and a bounded safe preview.
- Added OKX as an independently diagnosed exchange and made it the default in the example configuration.
- Preserved the read-only contract: no order placement, modification, or cancellation methods exist.
- Added v9.8.3 regression coverage and release documentation.

## 9.8.2 — Multi-Exchange Foundation: Bybit Read-Only

- Added a working read-only Bybit V5 adapter for health, Unified wallet balances, linear positions, open orders, and symbol rules.
- Added independent exchange health classification: CONNECTED, PUBLIC ONLY, NOT CONFIGURED, GEO BLOCKED, AUTH FAILED, and UNAVAILABLE.
- Binance HTTP 451 restricted-location responses now report GEO BLOCKED instead of PUBLIC ONLY.
- Added optional exchange routing to all exchange Telegram commands and `EXCHANGE_DEFAULT` configuration.
- Added safe endpoint diagnostics without exposing credentials.
- Preserved the read-only contract: no adapter can place, modify, or cancel orders.
- Added v9.8.2 regression tests and updated deployment documentation.

## 9.8.1 — Exchange Foundation: Binance Read-Only

- Added a typed, async, read-only `ExchangeAdapter` contract and normalized exchange models/errors.
- Added an environment-backed exchange registry that never persists API credentials.
- Added a working Binance USD-M Futures adapter for health, balances, positions, open orders and symbol execution rules.
- Added `/exchanges`, `/exchange_balance`, `/exchange_positions`, `/exchange_orders`, and `/exchange_symbol`.
- LIVE execution remains impossible by contract: no order write methods exist in this release.
- Added mocked exchange regression tests and deployment configuration documentation.

## 9.8.0 — Explainable Similarity Intelligence

- Added feature-level similarity explanations with weighted Structure, Liquidity, Market, Indicators, and Execution breakdowns.
- Added matched-feature and difference attribution for every closest Replay.
- Added aggregate top matches, largest differences, average similarity, and sample-based statistical confidence.
- Corrected report semantics so `Found` and performance metrics use the complete qualifying history while Telegram output remains bounded.
- Added `/genome [signal_id]` for grouped inspection of the normalized Strategy Genome and fingerprint.
- Preserved leakage protection: only closed paper and resolved zero-exposure shadow outcomes are eligible.
- Preserved all copy guardrails, portfolio accounting, adaptive policy boundaries, and fail-closed LIVE execution.
- Added v9.8 regression coverage and updated production documentation.

## 9.7.0 — Strategy Genome & Similar Trade Intelligence

- Added deterministic Strategy Genome snapshots for every accepted and rejected copy attempt.
- Added full-context similarity scoring across structure, liquidity, regime, timeframe, setup, indicators, volatility and execution features.
- Added leakage-safe search over closed paper positions and resolved zero-exposure shadow outcomes only.
- Added `/copy_similar [signal_id]` with Win Rate, average R, MFE, MAE and closest Replay IDs.
- Added indexed genome persistence with additive SQLite/PostgreSQL-compatible migrations.
- Preserved all guardrails, adaptive policy limits and fail-closed LIVE execution.
- Added v9.7 regression coverage; full suite passes with 102 tests.

## 9.5.0 — Guardrail Outcome Intelligence

- Added zero-exposure shadow lifecycle tracking for rejected paper executions.
- Added counterfactual R attribution by rejection guardrail.
- Added `/copy_guardrails` report for losses avoided, wins missed, and net shadow expectancy.
- Added additive database migration fields with PostgreSQL and SQLite compatibility.
- Preserved fail-closed LIVE execution and all existing risk limits.


## 9.4.0 — Execution Intelligence & Rejection Analytics

- Added a read-only copy-execution decision funnel for accepted and rejected attempts.
- Added ranked rejection diagnostics by guardrail code, symbol, and timeframe.
- Added dominant rejection reason to copy status and statistics.
- Added `/copy_rejections` with recent rejected attempts and 30-day acceptance rate.
- Kept all analytics observational: no guardrail is weakened automatically.
- Added v9.4 regression coverage and release documentation.

## 9.3.0 — Copy Training Foundation

- Added leakage-safe `CopyTrainingService` trained exclusively on closed paper executions.
- Added conservative Bayesian cohort policy with minimum samples, bounded confidence adjustment and bounded risk scaling.
- Integrated adaptive policy into the fail-closed execution validator.
- Added persistent negative-cohort rejection through `NEGATIVE_COHORT_EDGE`.
- Added training metadata to execution audit events.
- Added `/copy_training` reporting for readiness and cohort performance.
- Added v9.3 regression coverage and release documentation.

## 9.2.0 — Copy Execution Ledger & Portfolio Guardrails

- Rebuilt paper copy execution around an idempotent lifecycle ledger.
- Enforced daily realized-loss limits, portfolio heat, maximum simultaneous positions, duplicate-symbol protection, and post-trade cooldowns.
- Enforced minimum signal confidence, maximum activation slippage, and maximum notional exposure per position.
- Added equity-aware sizing: new trades use paper balance plus realized PnL rather than a static balance.
- Added correct partial-fill accounting for TP1 and TP2, including realized R and realized PnL deltas.
- Added event-level PnL ledger entries for opens, partial fills, closes, rejections, and panic closes.
- Added execution statistics for equity, daily PnL, total PnL, win rate, average R, and rejection counts.
- Added `/copy_guard` for confidence, notional, cooldown, and slippage guardrails.
- Added forward-compatible database migrations for all new profile, position, and event fields.
- Added v9.2 regression coverage and a full ACTIVE → TP1 → TP2 → TP3 database smoke test.

## 9.1.0 — Runtime Integrity & Resilient Analysis Core

- Stabilized runtime imports and analysis dependencies.
## v9.9.10 — Durable Live Execution Foundation

- Added a normalized, capability-discoverable exchange execution contract whose unsupported operations fail explicitly.
- Added durable live execution, submission-attempt, fill, account-configuration, and readiness-audit records with stable client order IDs.
- Added a fail-closed live state machine: ambiguous submissions enter recovery and are queried by client ID before any further action.
- Added idempotent fill ingestion, weighted average price and commission aggregation, reduce-only close validation, bounded deterministic retries, and account isolation.
- Added PAPER, SHADOW, LIVE_DRY_RUN, LIVE and DISABLED modes. PAPER remains the default; dry-run never places orders; LIVE requires external enablement plus every readiness gate.
- Added scoped Telegram readiness, dry-run, two-step confirmation, emergency-disable, and recovery diagnostics. Confirmation alone cannot enable LIVE.
## v9.9.11 — BingX Production Adapter Certification

- Certified the existing BingX Swap v2/v3 adapter behind the normalized live-execution contract for explicit `prod-live` and `prod-vst` environments.
- Added authenticated account/mode synchronization, server-time offset handling, symbol/order normalization, leverage and margin synchronization, production order/query/cancel/fill operations, and explicit capability discovery.
- Added read-only `LIVE_DRY_RUN` reports and separately classified VST economic certification with explicit server enablement, private confirmation, durable expiry, and zero-exposure verification.
- Added durable BingX certification audits, synchronized account metadata and normalized symbol-rule caching without storing raw exchange payloads.
- Added `/live_sync`, `/live_certify`, and `/live_account` operations. LIVE remains disabled by default and requires a recent VST economic certificate plus all v9.9.10 gates.
- Hardened additive PostgreSQL startup migrations against concurrent rolling-deploy column races.
- Added bounded Telegram webhook backpressure so overload returns a retryable response instead of accepting work into an unbounded task set.
- Added production query indexes for due/expired execution claims, account synchronization and readiness history.
- Documented stabilization invariants, rollback constraints, remaining debt and the staged GPT-trading readiness plan. No GPT or LIVE behavior was enabled.

## v9.9.12 — GPT Trading Intelligence Shadow Layer

- Added explicit `AI_OFF`, `AI_OBSERVE`, `AI_SHADOW`, `AI_ASSIST`, and fail-closed `AI_GATED` modes; `AI_SHADOW` is the default.
- Added a provider-neutral structured HTTP contract with bounded timeouts, retries, concurrency, token/cost limits, duplicate suppression, deterministic abstention and a durable circuit breaker.
- Added immutable, idempotent AI decisions with prompt/model identity, checksums, concise evidence, schema status, latency, usage and fixed-scale cost storage.
- Added strict response validation for stale context, malformed JSON, unsupported fields/actions, hallucinated symbols, impossible prices, missing evidence and unsafe confidence/risk values.
- Added separate signal-quality, deterministic-policy, execution, intervention and AI counterfactual outcome fields plus calibration, Brier score and ECE metrics.
- Added `/ai_status`, `/ai_mode`, `/ai_decision`, `/ai_explain`, `/ai_metrics`, `/ai_cost`, `/ai_compare`, and `/ai_disable` operator commands.
- Added a bounded asynchronous shadow worker and AI runtime diagnostics. AI cannot call exchange adapters or lifecycle mutation services and does not delay execution.

## v9.9.13 — Structured AI Outputs

- Added explicit, diagnostic provider capabilities rather than inferring model behavior from provider names.
- Added a strict Draft 2020-12 AI decision schema with no additional properties and sent it through provider-native strict structured-output parameters.
- Added separate OpenAI-compatible Chat Completions and OpenAI Responses provider protocols with normalized response, usage, request-ID and cost metadata.
- Added capability-driven `max_tokens`/`max_completion_tokens`, temperature omission, strict-schema selection and recorded JSON-object fallback.
- Added staged output validation, semantic consistency checks, market-truth checks, and non-retryable handling of malformed or invalid model output.
- Added additive decision metadata for schema/context/request versions, output modes, downgrade reason, validation stage, pricing status, cached/reasoning tokens and provider request identity.
- Changed missing pricing from authoritative zero to `UNPRICED`; production requests fail closed when pricing is required but unavailable.
- Expanded Telegram and runtime diagnostics with protocol, capabilities, schema/semantic validity, downgrades, p95 latency, cost status and compatibility failures.
## v9.9.14 — AI Provider Certification and Shadow Evaluation

- Added identity-bound, expiring provider certification covering configuration, authentication, structured schema, usage, pricing, latency, request IDs and capability compatibility.
- Added durable governance audit events and a database-backed global kill switch that blocks AI calls immediately and survives restarts.
- Added rolling 1h/24h/7d/30d telemetry, latency percentiles, drift checks, counterfactual cohorts, abstention quality and calibration confidence intervals.
- Added deterministic shadow-experiment assignment and additive certification, governance, drift, experiment and cost-reconciliation schema.
- Added `/ai_provider`, `/ai_certification`, `/ai_drift`, `/ai_experiments` and admin-only `/ai_kill` diagnostics.
- Preserved deterministic execution isolation; AI remains advisory and `AI_GATED` remains unavailable.

## v9.9.15 — OpenAI Provider Certification Readiness

- Normalized Chat Completions and Responses envelopes into extracted structured text, one parsed payload, request/model/usage metadata, and explicit extraction status without persisting raw bodies or reasoning items.
- Replaced protocol-specific certification shape checks with the exact production JSON Schema, domain, market-truth, and semantic validation pipeline.
- Made Responses extraction order-independent across reasoning and message items; refusals, incomplete output, missing messages, and malformed content fail with normalized stage/code diagnostics.
- Added explicit, durable certification states and timestamps, one-attempt paid probes, per-identity repeat suppression, request/usage/cost evidence, and identity-change invalidation.
- Bound decisions, circuit state, telemetry, promotion evidence, and governance to the exact provider/protocol/endpoint/model/prompt/schema/context/request/pricing/capability/output/reasoning identity.
- Isolated immutable legacy and disabled-provider decisions from current-identity promotion; added persisted scoped evidence and configurable promotion thresholds.
- Added bounded observation depth, queue-drop and duplicate-suppression audits, identity-scoped Telegram diagnostics, and Responses-first safe deployment defaults.
- Added GPT-5.6 cache-write token normalization and externally versioned 1.25x cache-write pricing so cost limits cannot silently omit prompt-cache writes.
- Preserved zero AI execution authority and kept `AI_GATED` unreachable.
## v9.9.16 â€” Production Copy Trading & Research Foundation

- Completed automatic PAPER copy from signal eligibility through per-user configuration, deterministic validation/sizing, durable execution queue, order/fill, unified position lifecycle, accounting projection and restart recovery.
- Added Conservative, Standard, Aggressive and Custom profiles; fixed, risk, equity-percent and fail-closed trusted-source proportional sizing; symbol/timeframe/setup/direction filters; portfolio exposure and daily-loss controls.
- Made copy activation atomic, preserved lifecycle tracking after disable, kept panic user-scoped, separated manual/panic outcomes from pure strategy metrics and retained operator-only reconciliation diagnostics.
- Applied PAPER taker commission to automatic TP/SL/partial/panic exits so ledger fees and actual net PnL include both sides without altering price-path realized R.
- Added immutable decision-time research snapshots, append-only versioned terminal outcomes, descriptive cohort metrics, overlapping deterministic regimes, transparent diagnostic rankings and explicit late-backfill quality labels.
- Added four versioned SHADOW Strategy Lab baselines over identical snapshots and verified they have no order or execution authority.
- Added realistic 1m/3m/5m after-cost research using explicit fee, spread, slippage and latency assumptions with minimum-sample gates and no profitability claims.
- Added centralized capability metadata, a lease-protected bounded research worker, restart-safe backfill, Telegram research dashboards and additive PostgreSQL/SQLite schema.
- Bound AI similarity and adaptive-learning indicators to immutable decision-time research snapshots; persisted manual/intervened counterfactuals but excluded them from headline learning metrics.
- Hardened OpenAI Responses extraction for reasoning-first/reordered output, assistant message arrays, SDK aggregates, native parsed structured output, refusals and normalized incomplete reasons. No reasoning traces are persisted.
- Preserved `AI_GATED` as `AI_OFF`, preserved LIVE fail-closed behavior and made no automatic paid provider request.

# LiquidityVisionBot v9.9.16 — Production Copy Trading & Research Foundation

## Release scope

v9.9.16 completes the automatic PAPER copy product and introduces an operational, leakage-controlled research foundation. It also hardens the OpenAI Responses extraction pipeline needed for a manual GPT-5.6 Terra certification run.

The release does not enable real-money LIVE execution, does not grant AI or research any execution authority, does not enable `AI_GATED`, and does not make an automatic paid provider request.

## Automatic PAPER copy path

```text
eligible signal
  -> per-user enabled profile and filters
  -> deterministic portfolio/risk validation
  -> fixed, risk, equity-percent, or trusted-source proportional sizing
  -> immutable execution plan and idempotency key
  -> persistent execution journal and expiring queue claim
  -> PAPER adapter order
  -> idempotent fill
  -> unified paper position
  -> exactly-once TP/SL/partial/panic lifecycle events
  -> append-only portfolio ledger
  -> compatibility projection and separated statistics
```

`/copy_enable` atomically enables automatic PAPER entries. `/copy_disable` stops new entries while existing positions remain tracked through terminal lifecycle. `/panic` is user-scoped, disables that user's copy profile, closes their open PAPER positions, and is excluded from pure strategy statistics.

The global `COPY_EXECUTION_ENABLED=false` switch prevents new economic PAPER entries but does not strand existing lifecycle work. Queue retries use stable idempotency keys and leased claims, so restart or duplicate polling cannot submit the same PAPER order twice. Any non-certified LIVE path remains fail-closed in the separate live execution stack.

### Profiles and controls

- `CONSERVATIVE`, `STANDARD`, `AGGRESSIVE`, and `CUSTOM` templates.
- Risk-percent, fixed-USDT, equity-percent, and trusted-source proportional sizing.
- Per-order notional, portfolio exposure, maximum positions, remaining-risk heat, daily realized loss, confidence, slippage, and symbol cooldown limits.
- Canonical symbol whitelist/blacklist, timeframe, setup, direction, and experimental-setup filters.
- Quantity is rounded down; a minimum-notional rule never increases risk to force an order through.
- Multi-user state, journals, orders, fills, positions, lifecycle events, ledgers, commands, panic, and metrics remain scoped by Telegram user ID.

## Accounting and performance separation

Unified execution positions and the append-only portfolio ledger remain the authority for new PAPER executions. The legacy `paper_positions` table is a compatibility projection, not a second lifecycle writer. `PORTFOLIO_ACCOUNTING_SOURCE=SHADOW` remains the safest default for deployment; `UNIFIED` is available only when its existing risk-resolution checks pass.

Automatic PAPER entry and service-driven TP/SL/partial/panic exits apply the same configured lifecycle taker-rate assumption. Realized R remains a price-path metric; actual net PnL subtracts all accumulated entry and exit commissions exactly once through idempotent ledger source keys.

Copy reporting separates:

- deterministic eligibility, acceptance, and rejection;
- pure non-intervened strategy W/L/BE, expectancy, average win/loss, profit factor, and drawdown proxy;
- actual gross PnL, fees, net PnL, fill count, and slippage;
- manual and panic intervention counts and realized R.

Normal `/copy` and `/copy_stats` output uses product language. Reconciliation internals remain available to configured administrators through `/copy_diagnostics`.

## Research Engine

Each newly recorded eligible signal receives one immutable decision-time snapshot. Existing signals are backfilled in bounded batches and explicitly labeled `LATE_TERMINAL_BACKFILL`; they are never silently represented as true contemporaneous observations. Snapshot identity, source checksum, feature version, decision time, capture quality, session, confidence bucket, regimes, setup, plan geometry, and features are persisted without outcome fields.

After a signal resolves, the worker attaches an append-only, content-addressed outcome version containing:

- signal result, realized R, MFE, MAE, and TP progression;
- stop outcome and lifecycle timestamps;
- deterministic copy-policy outcomes and policy counterfactual R where supported;
- actual per-user unified execution R/PnL, fees, slippage, close reason, and intervention flag;
- a no-intervention result only when the independent signal lifecycle supplies one.

Manual/panic outcomes remain persisted and queryable but are excluded from pure strategy, cohort, AI counterfactual, and adaptive-learning metrics. AI similarity and indicator learning read the immutable `DECISION_TIME` research snapshot rather than mutable post-close signal features.

The cohort engine reports sample size, win rate, expectancy R, average win/loss, profit factor, MFE, MAE, drawdown proxy, and a minimum-sample status by strategy, timeframe, direction, primary regime, confidence bucket, symbol, UTC session, and major feature combination. Output is descriptive and explicitly makes no causal or future-profitability claim.

## Strategy Lab, regimes, and rankings

Four versioned strategies run in SHADOW over the same persisted signal snapshots:

- `LIQUIDITY_SMC`
- `TREND_FOLLOWING`
- `BREAKOUT`
- `MEAN_REVERSION`

They store action, direction, confidence, hypothetical plan fields, evidence, version, and checksum. The module does not import execution, order, sizing, live-account, or exchange services and cannot open another position.

Regime classification supports overlapping `TREND_UP`, `TREND_DOWN`, `RANGE`, `COMPRESSION`, `BREAKOUT`, `HIGH_VOLATILITY`, and `LOW_VOLATILITY` tags, falling back to `UNKNOWN`. A primary tag exists only for convenient grouping; the full overlapping set remains in the immutable snapshot.

Diagnostic signal ranking stores a transparent score and component breakdown for confidence, reward/risk, regime alignment, feature evidence, and capture quality under a versioned rank definition. It has no admission or execution effect.

## Scalping research

Resolved 1m/3m/5m non-intervened samples are evaluated after explicit round-trip taker fees, spread, per-side slippage, and latency penalty. The engine converts the configured movement cost into R using the decision-time stop distance, reports after-cost expectancy, and refuses a positive classification until both expectancy and the configured minimum sample size pass. This is PAPER/SHADOW research only.

## OpenAI Responses completion fix

Certification and production observations use the same provider extraction and validation pipeline. Responses extraction is order-independent and supports:

- reasoning items before or after assistant items without persisting reasoning content;
- the final assistant message anywhere in `output`;
- assistant `content` arrays with `output_text`, native parsed JSON, and refusal parts;
- SDK top-level `output_text` and `output_parsed` aggregates;
- future top-level output-text items;
- structured `json_schema` payloads and direct normalized test transports;
- deterministic incomplete reasons for max-output-tokens, content filtering, and unknown incomplete states.

The configured observation and certification output budget is 1200 tokens because GPT-5.x Responses accounts reasoning and final structured output inside `max_output_tokens`. The recommended provider timeout is 45 seconds so low-effort reasoning plus schema generation is not cut off by the earlier 15-second observation default. Extraction logs only the selected path, status, normalized reason, and checksums; raw response bodies, assistant output, refusals, secrets, and hidden reasoning are not persisted.

No provider is contacted during startup or deployment. `/ai_certification run` remains the single explicit paid probe and must be invoked manually after configuration review.

## Schema and migration

Startup creates additive SQLite/PostgreSQL-compatible tables and indexes for:

- AI observation intelligence, similarity, counterfactual, learning, queue, and request telemetry;
- immutable research snapshots, versioned outcomes, strategy decisions, and diagnostic rankings;
- centralized capability entitlements;
- expanded copy profile controls and profile audit events.

Existing rows and legacy lifecycle tables are not deleted or rewritten. Re-running startup is idempotent. Rollback to v9.9.15 can ignore the additive tables and columns; do not drop them during rollback. Profiles explicitly disabled before rollback remain disabled.

## Runtime and restart behavior

`ResearchWorker` is bounded by `RESEARCH_BATCH_LIMIT`, sleeps for `RESEARCH_INTERVAL_SECONDS`, uses a cross-process distributed lease, records runtime heartbeat/error state, and runs blocking database projection work off the event loop. Immediate research capture and all worker failures are logged and isolated from signal production, deterministic policy, copy execution, lifecycle, and accounting.

The Render monitor cycle includes one research pass, so an external cron can progress research on a sleeping free service. Duplicate capture, strategy, rank, outcome, order, fill, lifecycle, ledger, and AI observation work is protected by unique identities or claims.

## Telegram commands

Copy product:

- `/copy`, `/copy_enable`, `/copy_disable`, `/copy_profile`
- `/copy_size`, `/copy_risk`, `/copy_limits`, `/copy_guard`
- `/copy_symbols`, `/copy_filters`, `/copy_queue`
- `/orders`, `/fills`, `/positions`, `/copy_stats`, `/panic`
- admin-only `/copy_diagnostics`

Research product:

- `/research`
- `/strategy_lab` or `/strategy_compare`
- `/regimes`
- `/edge_report`
- `/signal_rankings`
- `/scalping_research`
- `/capabilities`

AI observation additions:

- `/ai_dashboard`, `/ai_history`, `/ai_regimes`, `/ai_similarity`
- `/ai_learning`, `/ai_statistics`, `/ai_provider_health`, `/ai_counterfactual`

## Deployment configuration

Deploy the additive migration with `APP_VERSION=9.9.16`, `EXECUTION_MODE=PAPER`, `LIVE_EXECUTION_ENABLED=false`, and `AI_TRADING_MODE=AI_OFF`. Keep `AI_GATED` unused. Recommended new variables are documented in `.env.example` and `render.yaml`.

After deployment, confirm `/admin_status`, `/workers`, `/copy`, `/copy_diagnostics`, `/research`, `/strategy_lab`, `/regimes`, `/signal_rankings`, `/scalping_research`, `/ai_provider`, and `/ai_certification` before activating provider observation. Do not run `/ai_certification run` until a deliberate paid request is intended.

## Rollback

1. Set `COPY_EXECUTION_ENABLED=false` to block new PAPER entries while allowing existing lifecycle tracking.
2. Set `AI_TRADING_MODE=AI_OFF`, `AI_PROVIDER=disabled`, and use the durable AI kill switch if a provider had been enabled.
3. Leave `LIVE_EXECUTION_ENABLED=false`.
4. Deploy v9.9.15 code without dropping additive schema.
5. Verify unified/legacy parity and existing PAPER positions before any later reactivation.

Research and AI projections are advisory data. Their failure or rollback cannot authorize, duplicate, size, or mutate an economic action.

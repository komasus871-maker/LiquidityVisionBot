# v9.9.11 Production Hardening and GPT Trading Readiness

This document records the final stabilization boundary for v9.9.11. It does not enable LIVE execution and does not introduce GPT-directed trading.

## Operational invariants

- `paper_execution_positions` and `paper_portfolio_ledger` are authoritative for unified paper accounting. Legacy `paper_positions` is a compatibility projection only.
- Copy plans are deterministic and reserved by a unique idempotency key. Queue claims are atomic and leased; paper orders, fills, lifecycle events and ledger events each have independent unique source identities.
- LIVE submission is fail-closed behind deployment flags, account confirmation, limits, certification, synchronization, recovery, reconciliation and kill-switch gates.
- Ambiguous economic submissions are never blindly retried. Exchange truth must be resolved by stable client order identity before state advances.
- PostgreSQL is required in production. SQLite is a local/test compatibility backend, not a multi-worker production database.

## Deployment and rollback

Startup schema changes are additive and transactional on PostgreSQL. Concurrent rolling starts tolerate a duplicate-column race. Rollback may retain additive columns and indexes; older code does not depend on their absence. Keep `LIVE_EXECUTION_ENABLED=false`, `BINGX_PRODUCTION_ADAPTER_ALLOWED=false`, and the account kill switch active until an operator explicitly completes every readiness gate.

Webhook work is bounded by `WEBHOOK_MAX_ACTIVE_UPDATES` (default 100). At capacity the service returns HTTP 503 before recording the update identity, allowing Telegram to retry rather than silently losing accepted work.

## Remaining production constraints

- Monetary storage predating the live foundation uses `DOUBLE PRECISION`. Calculations use `Decimal` at exchange boundaries, but a future online migration to fixed-scale `NUMERIC` is still desirable before broad autonomous LIVE rollout.
- The live coordinator is a certified foundation, not an always-on reconciliation worker. Continuous order/fill reconciliation and operator alerting must be proven under controlled VST soak tests before autonomous LIVE use.
- Schema evolution is centralized in `create_tables`; a versioned forward/down migration tool remains preferable for zero-downtime multi-version deployments.
- Historical modules and release documents are retained for compatibility and audit history. Removal should follow import/usage telemetry, not a stabilization release.

## GPT trading integration plan

GPT output must remain advisory until each stage below is independently measurable and reversible.

1. **Immutable context contract** — provide normalized market regime, liquidity, volatility, portfolio exposure, signal lineage and data freshness. Reject stale, incomplete or contradictory context.
2. **Append-only decision memory** — store prompt/model/version hashes, inputs, proposed action, confidence, rationale, policy result, human action and eventual outcome. Never let generated text rewrite execution truth.
3. **Shadow evaluation** — run GPT proposals beside the deterministic engine with no execution authority. Measure calibration, abstention quality, drift, latency, cost and counterfactual PnL over multiple regimes.
4. **Deterministic policy gate** — translate proposals into a strict typed intent. Existing sizing, exposure, stop, symbol, readiness and idempotency validators remain authoritative and cannot be bypassed by the model.
5. **Human approval mode** — require a time-limited Telegram approval bound to the exact immutable intent checksum. Any market/context change invalidates approval.
6. **Constrained autonomous mode** — begin with a small allowlist, low notional, low leverage, daily loss cap, per-symbol cooldown, global kill switch and automatic fallback to shadow mode on drift, timeout, schema mismatch or reconciliation uncertainty.
7. **Continuous review** — score confidence calibration, rejected-intent quality, outcome attribution and model/version regressions. Promotion or rollback is a human-controlled release decision.

Required components before autonomous authority: a typed `AIDecisionProposal`, immutable context snapshot repository, model/prompt registry, evaluation ledger, policy decision trace, approval token store, shadow comparison reports, drift alarms, budget/rate limits, prompt-injection isolation, and a tested global AI-disable switch.

The execution service should consume only a policy-approved typed intent; it must never parse free-form model prose or call a model inside an exchange transaction.

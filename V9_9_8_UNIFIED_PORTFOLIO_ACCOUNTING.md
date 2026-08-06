# v9.9.8 — Unified Portfolio Accounting

## Authority and formulas

`ExecutionPortfolioEngine` is the normalized portfolio read model. Unified order/fill/position/lifecycle repositories remain the execution authority. `paper_execution_positions` authorizes open quantity, exposure, lifetime realized PnL/R, unrealized PnL and lifetime commissions. Closed rows remain in this table, so realized results do not disappear when a position closes. `copy_profiles.paper_balance` is starting paper balance.

- `net realized PnL = sum(position.realized_pnl) - sum(position.total_commission)`
- `net equity = starting paper balance + net realized PnL + open unrealized PnL`
- `gross exposure = sum(abs(open quantity × last price))`
- `net exposure = sum(direction × open quantity × last price)`, where long is +1 and short is -1
- `realized R = sum(position.realized_r)`
- `confirmed heat R = sum(open quantity / initial quantity)` only for trustworthy risk rows

Commissions are subtracted exactly once. Position totals are used for lifetime accounting; fills and lifecycle events are supporting evidence and are not summed again.

## Portfolio ledger

`paper_portfolio_ledger` is append-only and keyed by stable `source_key`. It records `REALIZED_PNL`, `COMMISSION`, and supports `BALANCE_ADJUSTMENT`/future funding entries without schema redesign. Fill and close writes occur in the same transaction as their economic mutation and use `ON CONFLICT DO NOTHING`, so crash recovery and replay cannot duplicate money. Upgraded historical positions are not assigned fabricated ledger timestamps.

Daily realized result uses UTC calendar-day semantics and equals today's ledger realized PnL minus today's commissions. This survives restart and includes entry and exit fees occurring that day.

## Risk completeness

Open rows are classified as complete, partial, missing, or invalid. Complete and partial require positive entry, stop, initial quantity and initial risk, a valid side, risk consistent with `abs(entry-stop) × initial quantity` within 2%, and remaining quantity no greater than initial quantity. Missing or invalid risk makes unified accounting unresolved. Missing risk is never treated as zero. Partial-close heat declines with remaining quantity.

## Cooldown and rejections

Cooldown is per user and normalized symbol, derived only from durable `CLOSED` unified lifecycle events and their actual persisted time. Rejected orders do not create positions or exposure; unified rejected-order count is diagnostic. Legacy rejected rows remain for historical analytics.

## Rollout, parity, and rollback

`PORTFOLIO_ACCOUNTING_SOURCE` accepts `LEGACY`, `SHADOW`, or `UNIFIED` and defaults to `SHADOW`. SHADOW computes unified and legacy state, deduplicates correlated signal identities, exposes parity, and fails closed if either risk model is unresolved. UNIFIED admission uses only resolved unified open count, heat, daily result, symbol state and cooldown. LEGACY is the rollback switch and restores legacy admission, daily result, and sizing equity. Legacy tables are retained but are shadow/rollback data; no parity process mutates unified state.

Parity currently distinguishes `MATCH`, `MISMATCH`, and `UNRESOLVED`; historical differences are expected where v9.9.7 lacks unified coverage. Live unresolved risk is dangerous and blocks admission.

## Migration and recovery

Schema evolution is additive and idempotent. Fresh initialization, v9.9.7 upgrade, repeated initialization, and interrupted replay are supported. The release adds the ledger, a lifecycle `commission_delta`, and owner/time and position indexes. It drops or renames nothing. Existing position totals remain immediately reportable; historical daily ledger reconstruction is deliberately not fabricated.

## Known limitations / v9.9.9

Historical legacy-only accounting, training, similarity, and guardrail analytics remain on compatibility paths until v9.9.9 backfill. Available funds is not reported because reserved margin/leverage collateral is not yet durably modeled. v9.9.9 should backfill normalized outcomes/ledger data with provenance, classify expected historical parity gaps explicitly, and only then consider legacy-table retirement.

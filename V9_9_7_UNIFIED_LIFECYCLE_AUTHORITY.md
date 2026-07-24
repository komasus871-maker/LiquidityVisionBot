# LiquidityVisionBot v9.9.7 — Unified Lifecycle Authority

## Purpose

This release makes `paper_execution_positions` the durable lifecycle authority
for every new paper execution while retaining `paper_positions` as a temporary
rollback-compatible projection.

## Authoritative flow

`planner → journal/queue → engine → order → fill → unified position → lifecycle event`

Automatic copy execution no longer reserves, claims, fills, and completes the
journal independently inside `CopyTradingService`. All new opens pass through
`CopyExecutionEngine`.

## Position lifecycle

Signal lifecycle changes are applied to unified positions through durable,
idempotent commands:

- `TP1` targets 50% remaining quantity.
- `TP2` targets 25% remaining quantity.
- Terminal signal states close the remaining quantity.
- Panic closes unified positions before updating the compatibility projection.
- Repeated delivery of the same signal transition is an economic no-op.

Every applied command writes one row to
`paper_position_lifecycle_events`, keyed uniquely by the lifecycle command.

## Compatibility and rollback

- Legacy-only historical positions continue through the old lifecycle fallback.
- New unified lifecycle mutations are projected into `paper_positions` for
  existing analytics, training, equity, cooldown, and rollback compatibility.
- Compatibility `execution_events` use unique source keys, so an authoritative
  event can be replayed after a crash without duplicating accounting effects.
- `PortfolioReconciliationService` no longer closes a legacy projection when a
  matching unified position remains open. It reports a lifecycle mismatch until
  the unified lifecycle worker applies the terminal signal.
- All schema changes are additive.
- Pre-release unified rows missing lifecycle metadata are reported diagnostically
  but do not degrade health during the compatibility window.

Rollback can restore the v9.9.6.9 application because the compatibility
projection remains populated. New lifecycle event rows and columns are ignored
by the older runtime.

## Database changes

Additive columns on `paper_execution_positions`:

- `remaining_fraction`
- `realized_r`
- `close_reason`
- `last_signal_status`

New table:

- `paper_position_lifecycle_events`

New indexes support unified user/signal lookup and lifecycle event inspection.

## Safety invariants

- LIVE execution remains disabled.
- One deterministic plan creates at most one order and one unified position.
- A lifecycle command affects quantity and realized results at most once.
- `remaining_fraction` always equals `quantity / initial_quantity`, including
  during partial fills, and runtime diagnostics verify that invariant.
- Out-of-order signal regressions cannot increase or reopen position quantity.
- Reconciliation may diagnose divergence but cannot override a live unified
  lifecycle.

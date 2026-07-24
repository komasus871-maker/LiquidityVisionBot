# LiquidityVisionBot v9.9.5f — Execution Observability & Transition Audit

This release makes the existing Planner → Queue → Engine lifecycle inspectable without changing the fail-closed LIVE boundary.

## Added

- Persistent `execution_transition_events` audit table.
- Actor, reason code, reason, execution reference, metadata and timestamp for every new lifecycle transition.
- User-scoped execution lookup by journal ID, signal ID, plan ID, idempotency key or execution reference.
- `/orders` for recent execution plans.
- `/execution` and `/execution <reference>` for full plan and lifecycle details.
- `/fills` for completed paper-execution events.
- Read-only `ExecutionInspectionService` shared by Telegram UI and tests.

## State audit

New executions produce the auditable path:

`PLANNED → EXECUTING → EXECUTED`

Rejected plans are recorded as terminal `REJECTED`. Failed and cancelled transitions retain their error/reason metadata. Same-state idempotent replays do not duplicate timeline events.

## Compatibility

The existing journal table, queue, planner, engine result model and LIVE fail-closed behavior remain compatible. Historical journal rows remain visible, although events created before this release naturally have no transition history.

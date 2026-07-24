# v9.9.5e — Planner → Engine Integration

This release introduces a durable execution queue without adding a duplicate persistence model.
Approved and rejected `CopyExecutionPlan` objects are enqueued through the existing execution journal;
`PLANNED` rows are the recoverable work queue and remain protected by the existing idempotency key.

## Added
- `ExecutionQueueService` as the single Planner → Engine hand-off.
- Durable FIFO reads for `PLANNED` journal rows.
- Plan reconstruction from immutable `plan_json`.
- Queue draining through `CopyExecutionEngine`.
- Per-user status summaries and Telegram `/copy_plan` and `/copy_queue` commands.

LIVE execution remains fail-closed. This queue is intentionally compatible with the future recovery layer.

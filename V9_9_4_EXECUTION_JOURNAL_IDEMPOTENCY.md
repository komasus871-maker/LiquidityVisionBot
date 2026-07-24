# LiquidityVisionBot v9.9.4 — Execution Journal & Idempotency Foundation

This release adds the persistent boundary between planning and future automatic execution.

- `copy_execution_journal` stores the immutable plan snapshot, user/account scope, decision and lifecycle state.
- A unique `idempotency_key` guarantees that the same user/account/signal plan is reserved only once.
- Supported lifecycle states: `PLANNED`, `REJECTED`, `EXECUTING`, `EXECUTED`, `FAILED`, `CANCELLED`.
- Attempt count, external execution reference and last error are persisted for future retries and recovery.
- Existing paper execution reserves its plan and records execution transitions without enabling exchange auto-trading.

LIVE execution remains disabled and fail-closed.

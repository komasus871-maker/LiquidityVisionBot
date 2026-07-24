# LiquidityVisionBot v9.9.6 — Recovery & Reliability Engine

This release makes the durable copy execution queue recoverable across process crashes and Render restarts.

## Lifecycle additions

`PLANNED → EXECUTING → RETRY_WAIT → EXECUTING → EXECUTED`

Executions that exhaust their retry budget move to `DEAD_LETTER` and cannot execute again automatically.

## Reliability guarantees

- Atomic claims remain database-backed.
- Every claim has a worker identity and expiration time.
- Expired claims are recovered on the next worker cycle.
- Transient adapter exceptions use bounded exponential backoff.
- Terminal and idempotency protections from v9.9.5 remain active.
- Runtime heartbeat records expose worker health through `/admin_status` and `/workers`.

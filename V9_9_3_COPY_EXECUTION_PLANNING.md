# LiquidityVisionBot v9.9.3 — Copy Execution Planning Layer

v9.9.3 introduces the side-effect-free contract between a trading signal and future copy execution.

## Added

- `CopyExecutionPlanner` builds a deterministic plan from the signal, validated copy profile, equity, portfolio state, training policy, and optional exchange account.
- Approved plans contain quantity, notional, leverage, entry, stop loss, take profits, risk amount, sizing mode, slippage, training adjustments, and a profile snapshot.
- Rejected plans retain the same shape and expose a stable rejection code and reason.
- Every plan has a deterministic idempotency key scoped to user, signal, exchange account, symbol, and side.
- Future executors can require `auto_copy`; the planner then fails closed with `AUTO_COPY_DISABLED`.
- Existing paper copy execution now consumes the planner instead of calling the validator directly.
- Execution events include plan and idempotency metadata for later journal/queue integration.

## Safety boundary

The planner does not place, cancel, or modify exchange orders. LIVE execution remains disabled and fail-closed. This release prepares the contract consumed by the next execution-journal and demo-executor stages.

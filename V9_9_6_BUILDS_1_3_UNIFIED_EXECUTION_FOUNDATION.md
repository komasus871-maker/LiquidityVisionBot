# LiquidityVisionBot 9.9.6 Builds 1–3

## Build 9.9.6.1 — ExecutionContext Foundation

Introduces an immutable `ExecutionContext` carrying the plan, journal record,
order, fills, position, portfolio snapshot, worker identity and metadata through
one pipeline object. Existing plan-based entry points remain compatible.

## Build 9.9.6.2 — Unified Pipeline State Machine

Introduces a central pipeline stage model and validates legal transitions from
receipt and reservation through dispatch, order, fill, position and terminal
states. Existing journal and paper-order state machines are preserved.

## Build 9.9.6.3 — Execution Dispatcher

Adds one context-aware dispatch boundary between the execution engine and
adapters. Legacy adapters implementing `execute(plan)` continue to work, while
new adapters may implement `execute_context(context)`. LIVE stays fail-closed.

## Compatibility

- `CopyExecutionEngine.execute(plan)` remains available.
- Existing journal, queue, retry, lease and paper lifecycle schemas are unchanged.
- Telegram commands and persistent data remain backward compatible.

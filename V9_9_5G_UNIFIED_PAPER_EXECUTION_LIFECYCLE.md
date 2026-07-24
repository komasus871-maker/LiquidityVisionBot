# LiquidityVisionBot v9.9.5g — Unified Paper Execution Lifecycle

This release adds a persistent economic lifecycle beneath the existing execution journal.

## New persistent entities

- `paper_execution_orders`
- `paper_execution_fills`
- `paper_execution_positions`
- `paper_order_events`

## Lifecycle

`SUBMITTED → ACCEPTED → PARTIALLY_FILLED/FILLED`

Terminal states are protected. Fill keys, order keys, and position keys make repeated execution idempotent.

## Integration

Both the standalone `CopyExecutionEngine` and the production paper-copy path now write the same order/fill/position model. Existing journal and legacy paper-position records remain intact.

## Telegram

- `/orders`
- `/execution [reference]`
- `/fills`
- `/positions`

# LiquidityVisionBot v9.9.6.5

## Position Lifecycle & Mark-to-Market

- Added explicit position lifecycle states.
- Added durable mark-to-market updates for open paper positions.
- Added idempotent partial/full position closing with realized PnL and fee aggregation.
- Expanded repository queries for active and historical positions.
- Portfolio snapshots now expose net PnL after fees / equity delta.
- Preserved paper-trading and existing execution pipeline compatibility.

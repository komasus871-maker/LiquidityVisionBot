# Liquidity Vision Intelligence v9.5.0

## Guardrail Outcome Intelligence

Rejected paper executions now remain under zero-exposure shadow observation until the source signal reaches a terminal lifecycle state. The platform records the counterfactual R result and attributes it to the guardrail that rejected the trade.

### Safety invariants

- Shadow outcomes never create positions, quantity, notional, exposure, equity changes, or realized PnL.
- Existing risk guardrails are never weakened automatically.
- LIVE execution remains fail-closed.
- Only terminal source signals resolve a rejected attempt.

### Command

`/copy_guardrails` reports losses avoided, profitable trades missed, counterfactual net R, per-guardrail effectiveness, and recent resolved rejections.

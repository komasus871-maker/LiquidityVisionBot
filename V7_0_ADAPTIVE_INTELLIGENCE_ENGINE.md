# Liquidity Vision v7.0 — Adaptive Intelligence Engine

## Trade Health 2.0
- Live health now weights actual R-progress, TP1 progress, distance from stop, MFE giveback, break-even protection, structure and momentum.
- A trade close to TP1 is no longer downgraded solely because short-term momentum cools.
- Live intelligence emits an explicit suggested action: HOLD, HOLD / MONITOR TP1, PROTECT PROFIT, MOVE STOP / PROTECT PROFIT, REDUCE RISK, or MONITOR INVALIDATION.

## Probability Engine 2.0
- Historical percentages are hidden until at least 30 comparable completed trades exist.
- Small samples return `Statistical model disabled` with a transparent reason instead of misleading 0%/50% estimates.

## UX integrity
- Restored Why NOT callback and keyboard action.
- Restored compact planned-entry wording and visual scenario flow.

## Validation
- Full regression suite: 40 passed.

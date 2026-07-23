# LiquidityVisionBot v9.2.0 — Copy Execution Ledger & Portfolio Guardrails

v9.2.0 turns the existing paper copier into a deterministic execution subsystem suitable for validating the complete copy-trading lifecycle before live exchange adapters are enabled.

## Execution guarantees

Every signal transition is processed idempotently. Repeated worker cycles do not duplicate partial fills or realized results. TP1 and TP2 reduce the remaining position fraction and realize their own R and PnL deltas; terminal states realize only the remaining fraction.

## Portfolio protection

The execution gateway now fails closed on daily loss limit, portfolio heat, maximum open positions, duplicate open symbol, symbol cooldown, insufficient confidence, excessive entry slippage, invalid trade geometry, stale activation, and excessive notional exposure.

## Equity and ledger

Position sizing uses current paper equity: configured starting balance plus realized PnL. `execution_events` stores each realized PnL delta, making daily loss enforcement and audit reconstruction deterministic.

## User controls

`/copy_guard <confidence> <notional_pct> <cooldown_min> <slippage_pct>` updates the new execution guardrails. `/copy` and `/copy_stats` expose equity, daily PnL, total PnL, R performance, win rate, and current risk limits.

## Safety boundary

LIVE execution remains disabled. v9.2.0 is the production-grade paper and validation foundation upon which authenticated exchange adapters can be added without bypassing the same validator and ledger.

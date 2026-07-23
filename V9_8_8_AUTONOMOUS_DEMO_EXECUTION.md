# LiquidityVisionBot v9.8.8 — Autonomous Demo Execution Core

This release adds automatic, no-confirmation order execution for authenticated BingX demo accounts only.

## Safety contract

- Live credentials are rejected before every write operation.
- `DEMO_EXECUTION_ENABLED=true` is required.
- Existing v9.8.7 preflight limits remain mandatory.
- Orders are serialized through a manager lock.
- Deterministic client order IDs provide idempotency during one runtime.
- Duplicate portfolio orders remain blocked by preflight.
- Every attempt is written to a JSONL audit trail.
- Three consecutive failures open a circuit breaker for 60 seconds by default.
- `/demo_kill` immediately disables execution for the current runtime.

## Commands

- `/demo_order bingx BTCUSDT BUY MARKET 0.001 60000 3`
- `/demo_order bingx BTCUSDT BUY LIMIT 0.001 60000 3 59000`
- `/demo_cancel bingx BTCUSDT ORDER_ID`
- `/demo_status bingx BTCUSDT ORDER_ID`
- `/demo_kill`
- `/demo_resume`

No manual confirmation is requested. Live execution is not implemented.

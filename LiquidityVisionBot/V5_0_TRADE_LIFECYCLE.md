# Liquidity Vision v5.0 — Trade Lifecycle 2.0

## Added

- Automatic lifecycle: WATCHING → TRIGGERED → ACTIVE → TP1 → TP2 → TP3.
- Automatic terminal states: STOP, BREAKEVEN, INVALIDATED, EXPIRED.
- Automatic stop movement to entry after TP1 (`AUTO_BREAK_EVEN_AFTER_TP1=true`).
- Persistent effective stop, break-even time, exit price, realized R, result, highest/lowest price.
- Live progress messages for active trades with TP1/TP2/TP3 and stop-safety bars.
- Persistent MFE and MAE on every tracker cycle.
- Trade Replay command: `/trade <signal_id>`.
- Journal statistics for Break Even and average realized R.
- Direction-flip protection for stale opposite WATCHING/TRIGGERED ideas.
- Promotion consistency: the promoted signal uses the current analysis direction and reuses the existing open signal ID.

## New environment variables

```env
AUTO_BREAK_EVEN_AFTER_TP1=true
TRADE_PROGRESS_INTERVAL=900
TRADE_PROGRESS_STEP=20
```

`TRADE_PROGRESS_INTERVAL` is in seconds. `TRADE_PROGRESS_STEP` is the TP1 progress percentage step that can trigger a live update.

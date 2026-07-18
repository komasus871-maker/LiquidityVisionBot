# LiquidityVisionBot v8.0.3 — Lifecycle Data Integrity

## Statistics integrity
- Legacy INVALIDATED records without realized R are no longer counted as closed trades.
- MANUAL_STOP is counted only when the trade was activated and realized R exists.
- Closed, wins/losses, average R, MFE and MAE now share the same terminal-outcome predicate.

## Pre-entry lifecycle
- WATCHING plans no longer accumulate favorable Pre-MFE before their entry zone is touched.
- Favorable pre-activation movement is tracked only after TRIGGERED.
- TRIGGERED plans that run more than MAX_LATE_ACTIVATION_R beyond the entry zone are marked EXPIRED with result MISSED_ENTRY instead of activating late.
- Default MAX_LATE_ACTIVATION_R is 0.75.

## Runtime diagnostics
- Default application version is 8.0.3.
- Admin status labels database totals as global counts.
- Admin status includes the symbol, timeframe, error count and last error for failing watch rows.

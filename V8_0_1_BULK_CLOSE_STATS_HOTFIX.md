# LiquidityVisionBot v8.0.1 — Bulk Close & Statistics Integrity

## Commands

- `/closeall`
- `/trade all close`

Both commands close only activated positions in `ACTIVE`, `TP1`, or `TP2` for the requesting owner.
`WATCHING` and `TRIGGERED` plans are not treated as positions.

## Manual lifecycle semantics

- Closing `ACTIVE` / `TP1` / `TP2` creates terminal status `MANUAL_STOP`, stores exit price and realized R.
- Closing `WATCHING` / `TRIGGERED` cancels the plan as `MANUAL_CANCEL`, without fake PnL or realized R.
- Existing legacy rows with `result='MANUAL_STOP'` remain included in statistics.

## Statistics

- Closed Win Rate = profitable closed outcomes / all closed outcomes.
- Active trades are excluded from win-rate denominator.
- Manual closes are counted separately.
- Invalidations are split into pre-entry and post-activation categories.
- Average MFE, MAE and realized R include valid completed outcomes and manual closes.

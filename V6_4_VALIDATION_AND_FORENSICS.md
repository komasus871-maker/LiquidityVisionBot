# Liquidity Vision v6.4 — Validation & Loss Forensics Core

This release replaces the previous placeholder backtest loop with an event-driven
OHLC simulator designed for conservative strategy validation.

## Added

- Candle-by-candle execution without future data leakage.
- Entry expiry and maximum holding windows.
- TP1/TP2/TP3 partial allocation lifecycle.
- Optional break-even movement after TP1.
- Conservative same-candle stop/target resolution.
- Trading fee, slippage and funding estimates.
- Gross/net R, MFE, MAE, drawdown, profit factor, expectancy and losing streaks.
- Regime and direction breakdowns.
- Deterministic loss diagnosis (`LossForensicsEngine`).
- Validation gate that keeps unproven strategies in PAPER mode.

## Safety policy

A strategy is not eligible for `LIVE_VALIDATED` until it passes all configured
sample-size, expectancy, profit-factor and drawdown thresholds. Five live losses
must therefore be treated as research evidence, not as permission to increase
risk or add more unvalidated complexity.

## Next release target

v6.5 should consume v6.4 reports to recalibrate admission thresholds, split
setups into independent models, add walk-forward evaluation and replace generic
ATR stops with structural invalidation stops.

# LiquidityVisionBot v9.3.0 — Copy Training Foundation

v9.3.0 closes the loop between the deterministic paper execution ledger and future copy-trading decisions. The platform now derives conservative, user-specific execution policy from completed paper positions without enabling live trading.

## Leakage-safe learning

Only `paper_positions` with `status='CLOSED'` participate in training. Open, partial, rejected and future signal states are excluded. Historical cohorts are selected by side and at least one matching context dimension: symbol, timeframe or setup key.

## Conservative adaptation

The policy uses Bayesian shrinkage toward neutral priors, requires eight closed executions before adapting, bounds confidence changes to -15/+8 points, and bounds risk multipliers to 0.25–1.25. A cohort cannot be blocked before fifteen closed samples, and blocking requires a materially negative conservative expectancy bound.

## Execution integration

`ExecutionValidator` remains the fail-closed gateway. Training policy can:

- raise or lower the effective minimum-confidence threshold;
- scale approved paper position size while preserving stop geometry;
- reject persistently negative cohorts with `NEGATIVE_COHORT_EDGE`;
- write training sample size, expectancy and multiplier into execution-event audit details.

## User visibility

`/copy_training` reports sample readiness, win rate, average and total R, plus strongest and weakest sufficiently populated cohorts.

## Safety boundary

LIVE execution is still disabled. v9.3.0 is a training foundation operating exclusively on paper execution history.

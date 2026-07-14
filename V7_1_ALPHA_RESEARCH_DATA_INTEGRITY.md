# v7.1 Alpha Research & Data Integrity Engine

## Goals

This release prevents malformed market data and impossible fills from becoming
ACTIVE trades or training examples. It also introduces a reproducible research
dataset and performance summaries.

## Data integrity gates

- Planned entry must be inside the preferred entry zone.
- LONG/SHORT price geometry is validated before persistence.
- Duplicate, stale, missing, and impossible OHLC candles are rejected.
- Activation price must remain within a volatility/zone-based tolerance of the
  locked entry plan.
- Rejected activations are closed as `DATA_INTEGRITY_REJECTED` and excluded from
  research statistics.

## Alpha research

`services.alpha_research.AlphaResearchEngine` creates immutable feature/outcome
rows and reports:

- expectancy in R;
- profit factor;
- win rate;
- max drawdown in R;
- longest losing streak;
- average MFE/MAE;
- grouped results by setup, timeframe, regime, symbol, or direction;
- CSV and JSONL exports for later walk-forward and ML experiments.

The engine never treats data-integrity rejections as valid strategy outcomes.

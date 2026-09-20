# Stage B Alpha Challenger Cycle 2 Preregistration

**Status:** FROZEN BEFORE CYCLE 2 DEVELOPMENT OUTCOME ACCESS.

Cycle 1 produced no Development qualifier. Its dominant failure was admission
density: most variants emitted 0–28 family signals, and DecisionQuality
rejected only three matched signals across the complete search. Cycle 2 is the
single reasoned redesign allowed by the master program. It does not alter or
rescue Cycle 1 and does not inspect Validation, either Blind partition, or
`LEGACY_SEEN_TEST`.

## Immutable evidence and split

Cycle 2 reuses the five immutable Cycle 1 OKX `1h` datasets and their recorded
content hashes. Only candles through the Development settlement tail
`2023-09-07T23:00:00Z` may be loaded. Decision timestamps remain
`2022-09-10T04:00:00Z` through `2023-09-01T23:00:00Z`; the Validation and new
Blind boundaries remain unchanged and inaccessible during construction.

## Targeted causal features

The redesign adds only outcome-independent price-state features motivated by
Cycle 1 sparsity:

- prior 24/48/72-hour highs and lows, shifted one candle;
- 6-hour price movement normalized by current causal ATR;
- current true-range expansion versus the preceding 20 closed candles;
- the existing causal EMA structure, EMA slope/separation, 6h/24h returns,
  path efficiency, relative volume, and market-state output.

No future extrema, trade result, Validation row, or post-entry excursion is an
input. A breakout level never includes the decision candle.

## Frozen family map and 12-variant budget

Each family has `BASE`, `STRICT`, and `BROAD`; LONG and SHORT are independent.

### `D1_RANGE_BREAKOUT_LONG`

- `BASE`: close above prior 48h high, EMA50 > EMA200, positive 24h return,
  range expansion >= 1.10, relative volume >= 1.00.
- `STRICT`: close above prior 72h high, EMA separation >= 0.75 ATR, positive
  24h return, expansion >= 1.30, volume >= 1.20.
- `BROAD`: close above prior 24h high, positive EMA50 slope and 6h return,
  expansion >= 0.90, volume >= 0.80.

### `D2_RANGE_BREAKOUT_SHORT`

- `BASE`: close below prior 48h low, EMA50 < EMA200, negative 24h return,
  expansion >= 1.10, relative volume >= 1.00.
- `STRICT`: close below prior 72h low, EMA separation >= 0.85 ATR, negative
  24h return, expansion >= 1.40, volume >= 1.30.
- `BROAD`: close below prior 24h low, negative EMA50 slope and 6h return,
  expansion >= 0.95, volume >= 0.90.

### `D3_MOMENTUM_EXPANSION_LONG`

- `BASE`: 6h move >= +1.0 ATR, positive 24h return, close above EMA50,
  24h efficiency >= 0.35, volume >= 0.90.
- `STRICT`: 6h move >= +1.5 ATR, separation >= 0.50 ATR, efficiency >= 0.45,
  volume >= 1.10.
- `BROAD`: 6h move >= +0.7 ATR, positive EMA50 slope, efficiency >= 0.28,
  volume >= 0.75.

### `D4_MOMENTUM_EXPANSION_SHORT`

- `BASE`: 6h move <= -1.1 ATR, negative 24h return, close below EMA50,
  24h efficiency >= 0.38, volume >= 1.00.
- `STRICT`: 6h move <= -1.7 ATR, separation >= 0.60 ATR, efficiency >= 0.48,
  volume >= 1.20.
- `BROAD`: 6h move <= -0.8 ATR, negative EMA50 slope, efficiency >= 0.32,
  volume >= 0.85.

## Execution, costs, and gates

Entry, stop/target geometry, 120-bar holding limit, conservative intrabar
semantics, base/higher/stress costs, four chronological Development folds,
60-fill/40-cluster minimum, expectancy/PF/fold/cost/diversification/bootstrap
gates, neighbor-stability definition, and Validation/Blind promotion contract
are unchanged from Cycle 1. Every emitted family signal must pass actual
`TradePlanIntegrity`, `DecisionQualityEngine`, and canonical replay.

At most one Development qualifier may be frozen for Validation. If none
qualifies, Cycle 2 ends with `NO VERIFIED EDGE WITH CURRENT DATA/FEATURE SET`;
criteria will not be weakened and no further OHLCV candidate cycle will be
created.

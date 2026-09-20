# Stage B Alpha Challenger Cycle 1 Preregistration

**Status:** FROZEN BEFORE DATASET OUTCOME ACCESS.

This cycle begins only because frozen F1 is `F1_NOT_CONFIRMED`. It does not
attempt to rescue or modify F1. It does not access the existing Phase 3B
`BLIND_HOLDOUT` or `LEGACY_SEEN_TEST`, and it does not change production
Analyzer, DecisionQuality, PAPER, LIVE, copy-trading, or Telegram defaults.

## Objective and bounded budget

Cycle 1 tests four explicit price/OHLCV alpha mechanisms with at most three
variants per family: no more than 12 variants. Candidate construction and
selection use Development only. Exactly one frozen Development survivor may be
opened on Validation. Exactly one unchanged Validation qualifier may later be
opened on the new Blind block. Failure is recorded; the same visible block is
never reused as unseen evidence.

## Predeclared universe

The fixed `1h` universe is:

1. `BTC-USDT-SWAP`
2. `ETH-USDT-SWAP`
3. `SOL-USDT-SWAP`
4. `XRP-USDT-SWAP`
5. `DOGE-USDT-SWAP`

These are established, high-liquidity crypto perpetual markets chosen before
strategy results, not performance-ranked selections. A symbol may be excluded
only if OKX instrument availability or the Phase 2A continuity contract fails;
it may not be replaced because another symbol backtests better. The maximum
universe is five for Cycle 1.

The sole Cycle 1 timeframe is `1h`. Any future `15m` or `4h` work receives a
separate experiment identity and evidence contract.

## New immutable data request

Request public OKX closed candles from `2022-09-01T00:00:00Z` through
`2024-09-05T11:00:00Z`, inclusive, for each declared symbol. The period ends
immediately before the existing independent-F1 dataset. Provider, retrieval
time, exact row count, continuity status, first/last timestamp, content hash,
and materialization manifest must be frozen before any family outcome is
evaluated.

Fetching/materialization may see candle values only to verify integrity. Alpha
logic, variants, thresholds, splits, and gates in this document are frozen
first. No performance ranking participates in universe inclusion.

## Frozen temporal boundaries

All timestamps are UTC decision timestamps, inclusive. A 220-candle causal
warm-up precedes the first decision. A 144-hour embargo/settlement interval
separates roles.

| Role | Decision start | Decision end | Settlement/embargo through |
| --- | --- | --- | --- |
| `DEVELOPMENT` | 2022-09-10T04:00:00Z | 2023-09-01T23:00:00Z | 2023-09-07T23:00:00Z |
| `VALIDATION` | 2023-09-08T00:00:00Z | 2024-03-01T23:00:00Z | 2024-03-07T23:00:00Z |
| `BLIND_HOLDOUT` | 2024-03-08T00:00:00Z | 2024-08-30T23:00:00Z | 2024-09-05T00:00:00Z |

Development is subdivided into four equal-duration chronological reporting
folds after materialization using timestamp arithmetic only. Boundaries are not
moved in response to outcomes.

## Causal market-state representation

Every decision-time state is computed only from closed rows at or before the
decision candle:

- EMA50/EMA200 direction and normalized separation;
- EMA50 normalized slope;
- 6-hour and 24-hour returns;
- 24-hour path efficiency and directional persistence;
- ATR/price and 180-hour causal volatility percentile;
- 80-hour dealing-range location;
- confirmed two-left/two-right swing structure, where a swing is usable only
  after its two confirming candles have closed;
- BOS/CHOCH state;
- relative volume against the preceding 20 closed candles;
- current-candle displacement efficiency/expansion;
- buy-side/sell-side sweep state against prior closed highs/lows;
- compression, ranging, transition, trend, and volatile-expansion state.

No state uses a future extrema label, later trade result, future regime, or
post-entry excursion.

## Family map and variants

Every family emits `NO_SIGNAL`, `LONG_CANDIDATE`, or `SHORT_CANDIDATE` plus a
family ID, raw strength, causal evidence, blockers, compatible state, entry
concept, and invalidation concept. Families never execute. A candidate must
still pass market-data integrity, `DecisionQualityEngine`, portfolio/risk, and
canonical replay.

### `C1_TREND_PULLBACK_LONG`

Rationale: persistent bullish EMA structure with positive multi-horizon return
may retain continuation edge when price revisits, rather than chases far above,
the EMA50 and volatility is not extreme.

- `BASE`: EMA50 > EMA200; positive 6h and 24h return; close above EMA50 and no
  more than 0.75 ATR away; path efficiency >= 0.32; volume ratio >= 0.85.
- `STRICT`: separation >= 0.50 ATR; efficiency >= 0.40; distance <= 0.50 ATR;
  volume ratio >= 1.00.
- `BROAD`: positive EMA50 slope; efficiency >= 0.28; distance <= 1.00 ATR;
  volume ratio >= 0.70.

### `C2_TREND_PULLBACK_SHORT`

Rationale: bearish continuation is researched independently because downside
impulses and execution costs are asymmetric.

- `BASE`: EMA50 < EMA200; negative 6h and 24h return; close below EMA50 and no
  more than 0.65 ATR away; efficiency >= 0.36; volume ratio >= 0.95.
- `STRICT`: separation >= 0.60 ATR; efficiency >= 0.44; distance <= 0.45 ATR;
  volume ratio >= 1.10.
- `BROAD`: negative EMA50 slope; efficiency >= 0.32; distance <= 0.85 ATR;
  volume ratio >= 0.80.

### `C3_LIQUIDITY_SWEEP_REVERSAL_LONG`

Rationale: a sell-side stop run that closes back above the swept level may
create a causal long reversal when accompanied by bullish displacement and a
non-premium location. This is not F1's Trend-conflict-plus-CHOCH admission rule.

- `BASE`: sell-side sweep; bullish displacement; dealing-range percentile <=
  50; RSI <= 55; no volatile-expansion state.
- `STRICT`: sell-side sweep; moderate/strong bullish displacement; percentile
  <= 38; RSI <= 48; volume ratio >= 1.00.
- `BROAD`: sell-side sweep; bullish close; percentile <= 62; RSI <= 60; volume
  ratio >= 0.70.

### `C4_LIQUIDITY_SWEEP_REVERSAL_SHORT`

Rationale: a buy-side stop run that closes back below the swept level may
create a short reversal, with thresholds independent from the long family.

- `BASE`: buy-side sweep; bearish displacement; dealing-range percentile >=
  55; RSI >= 50; no volatile-expansion state.
- `STRICT`: buy-side sweep; moderate/strong bearish displacement; percentile
  >= 65; RSI >= 58; volume ratio >= 1.10.
- `BROAD`: buy-side sweep; bearish close; percentile >= 48; RSI >= 46; volume
  ratio >= 0.80.

## Entry, invalidation, and execution

Cycle 1 uses `NEXT_OPEN MARKET` only so family admission is isolated from limit
fill optimization. Entry is the next candle open with the frozen modeled
market slippage. Initial invalidation is `1.5 ATR` from the decision close,
floored at 0.35% of price, matching existing canonical geometry. The sole
target is `2.0 ATR`, floored at 1.2 times initial risk. No trailing, partial,
breakeven, or timeout variant is tuned in Cycle 1. The canonical 120-bar maximum
holding period and conservative stop-first same-candle rule remain unchanged.

## Fixed costs

- `BASE_COST`: fee `0.0005`, market slippage `0.0003`.
- `HIGHER_COST`: fee `0.00075`, market slippage `0.0005`.
- `STRESS_COST`: fee `0.0010`, market slippage `0.0010`.
- Funding: `NOT_MODELED`; this limitation must be reported.

## Development selection gate

A variant can become the single frozen Validation candidate only if all hold:

1. At least 60 nominal fills and 40 same-time/same-direction clusters.
2. Positive aggregate expectancy-R of at least `+0.15R` and net-PnL PF at
   least `1.25` after base costs.
3. At least three of four chronological Development folds are positive; no
   fold with at least ten fills has expectancy <= `-0.15R` or PF <= `0.75`.
4. Higher-cost expectancy-R remains positive; stress expectancy-R is no worse
   than `-0.05R`.
5. No symbol supplies more than 60% of fills or positive aggregate net R.
6. No single cluster supplies more than 15% of positive aggregate net R.
7. Both neighboring variants are reported; selection requires a broad stable
   region, not an isolated threshold spike.
8. Bootstrap 95% expectancy-R lower bound is above `-0.05R`.
9. Causal provenance, DecisionQuality passage, and exact replay parity are
   intact.

If multiple variants pass, choose the highest median fold expectancy-R; ties
break by lower drawdown, then larger cluster count. If none pass, no Validation
data is opened for Cycle 1.

For an operational, outcome-independent interpretation of gate 7, all three
variants in the same family must be reported, and both of the other variants
must have at least 30 nominal fills, positive base-cost expectancy-R, and
profit factor above 1.0. This clarification is frozen before Development
outcomes are computed; it does not alter any family threshold.

## Validation and Blind promotion gates

Before Validation access, the selected candidate identity and complete config
hash are frozen. Validation requires at least 30 fills and 25 clusters,
positive expectancy-R >= `+0.10R`, PF >= `1.20`, at least two positive
chronological halves, no catastrophic half, positive higher-cost expectancy,
and no symbol above 70% of fills or positive net R. Failure rejects the
candidate without Blind access.

Before new Blind access, the unchanged Validation qualifier is frozen. Blind
requires at least 30 fills and 25 clusters, positive expectancy-R, PF > 1,
positive higher-cost expectancy, no catastrophic temporal half, and intact
causal/replay integrity. Blind is evaluated once; no post-Blind modification is
permitted.

## Registry requirements

Every one of the 12 variants, including failures, records experiment ID,
candidate/config/parent hashes, code revision and dirty-tree identity, dataset
hashes, symbol/timeframe universe, split boundaries, family/variant count,
feature version, costs, decisions, DecisionQuality rejections, fills, folds,
uncertainty, MFE/MAE, clusters, result, and rejection reason.

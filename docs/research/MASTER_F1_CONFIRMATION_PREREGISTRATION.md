# Master F1 Independent-Confirmation Preregistration

**Status:** FROZEN BEFORE OUTCOME. The completed result is recorded separately
in [MASTER_F1_CONFIRMATION_RESULT.md](MASTER_F1_CONFIRMATION_RESULT.md); none of
the criteria or boundaries below were changed after outcome access.

This contract gives the frozen Phase 3C candidate a single independent test
before any new alpha architecture is considered. It does not alter the
existing Analyzer, DecisionQualityEngine, replay semantics, production
defaults, Phase 3A/3B/3C artifacts, or protected Phase 3B windows.

## Frozen identities

- Champion control: `PARENT_BASELINE_CONFIG` `84533946dfbbbf65`.
- Candidate: `F1_COUNTERTREND_REVERSAL_LONG` / candidate ID
  `c49803034d94792e` / configuration `c49803034d94792e`.
- F1 admission rule: retain only an existing `DecisionQualityEngine`-approved
  **LONG** record carrying both decision-time labels `Trend conflicts` and
  `CHOCH confirmation`. Entry, stop, targets, RR, costs and replay outcomes
  remain exactly those of the frozen parent decision.

## New evidence request and boundary

One immutable, public-OKX, 1-hour SWAP candle set will be materialized for
BTC-USDT-SWAP, ETH-USDT-SWAP and SOL-USDT-SWAP. The requested new window is
`2024-09-05T12:00:00Z` through `2025-09-05T11:00:00Z` (8,760 closed candles
per symbol), strictly preceding the existing Phase 3B/3C dataset beginning
`2025-09-05T12:00:00Z`. Provider, count, integrity result, retrieval time and
content hash will be written with the artifact. No confirmation experiment may
refetch it.

The request reserves the first 220 candles for causal Analyzer warm-up and
144 candles after each decision block for entry/holding settlement. If the
materialized dataset cannot meet the exact requested window and these reserved
periods with intact time integrity, F1 is `F1_INSUFFICIENT_NEW_EVIDENCE`; the
block boundaries and qualification requirements are not re-optimized.

## Fixed chronological confirmation blocks

Assuming the requested intact 8,760-candle window, the 8,106 usable decision
hours are divided into three equal 2,702-hour blocks. Timestamps are decision
timestamps (UTC), inclusive. The settlement window is not an evaluation block.

| Block | Decision start | Decision end | Settlement through |
| --- | --- | --- | --- |
| `CONFIRM_A` | 2024-09-14T16:00:00Z | 2025-01-05T05:00:00Z | 2025-01-11T05:00:00Z |
| `CONFIRM_B` | 2025-01-11T06:00:00Z | 2025-05-03T19:00:00Z | 2025-05-09T19:00:00Z |
| `CONFIRM_C` | 2025-05-09T20:00:00Z | 2025-08-30T09:00:00Z | 2025-09-05T09:00:00Z |

## Locked evaluation protocol

For each block, replay both the original frozen baseline and F1 using the same
closed candles, warm-up, clocks, market-context treatment, costs and canonical
replay engine. F1 is a read-only family-admission overlay after the parent
decision authority; it never bypasses `DecisionQualityEngine`.

The report must include decisions, approvals, fills, win rate, expectancy-R,
net PnL, net-PnL profit factor, max drawdown, MFE, MAE, holding time, symbol
mix, base/higher/stress costs, same-time/same-direction correlated clusters,
and deterministic chronological block-bootstrap uncertainty. The same metrics
are reported for the control, with no pooled headline that masks block results.

## Locked F1 qualification criteria

`F1_CONFIRMED` requires all of the following on genuinely new evidence:

1. At least 60 nominal simulated F1 fills in aggregate.
2. Positive aggregate out-of-sample expectancy-R and reconciled net-PnL PF
   greater than 1.0 after base modeled costs.
3. No catastrophic confirmation block (defined as a block with at least five
   F1 fills and non-positive expectancy-R or PF at or below 0.75).
4. Positive expectancy-R after the predeclared higher-cost stress; no
   confirmation block may collapse to PF at or below 0.75 under that stress.
5. At least two blocks with five or more fills and positive expectancy-R, and
   no evidence that one block supplies more than 75% of aggregate net R.
6. No single symbol supplies more than 75% of F1 fills or aggregate net R.
7. Cluster-adjusted evidence must contain at least 30 same-time/same-direction
   clusters; no single cluster may supply more than 20% of aggregate net R.
8. Dataset integrity and canonical causal replay must remain valid, with no
   missing/future leakage evidence.

If an integrity/replay prerequisite or the 60-fill/30-cluster requirement is
not met, the only allowed result is `F1_INSUFFICIENT_NEW_EVIDENCE`. Otherwise,
failure of any criterion is `F1_NOT_CONFIRMED`. Only `F1_CONFIRMED` permits
access to `BLIND_HOLDOUT`; neither result permits access to
`LEGACY_SEEN_TEST`.

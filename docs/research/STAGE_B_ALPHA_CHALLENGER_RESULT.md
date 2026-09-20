# Stage B Alpha Challenger — Final Bounded Result

## Decision

`NO VERIFIED EDGE WITH CURRENT DATA/FEATURE SET`

Both permitted challenger-generation cycles are complete. No candidate met the
predeclared Development promotion contract, so Validation, new Blind,
pre-existing `BLIND_HOLDOUT`, and `LEGACY_SEEN_TEST` were not opened. No
strategy was promoted and production defaults are unchanged.

## Immutable data

The fixed public OKX universe is BTC, ETH, SOL, XRP, and DOGE USDT swaps on
`1h`, 17,652 closed candles each from 2022-09-01T00:00:00Z through
2024-09-05T11:00:00Z. The dataset IDs are `d800796de20eaa9e`,
`31ff8dfcf9ecbd4b`, `babf2f6205a3d619`, `3f36cb73f7d7cc84`, and
`e3fcecb864cbfa2c`. Candidate construction read only the Development history
and settlement tail.

## Cycle 1 — existing causal features

| Candidate | Fills | Expectancy-R | PF | Result |
| --- | ---: | ---: | ---: | --- |
| C1 trend pullback LONG base | 2 | +0.0263 | 1.0409 | Rejected: sample/gates |
| C1 trend pullback LONG strict | 0 | 0 | — | Rejected: no sample |
| C1 trend pullback LONG broad | 16 | +0.3522 | 3.1810 | Rejected: 60-fill/40-cluster minimum |
| C2 trend pullback SHORT base | 0 | 0 | — | Rejected: no sample |
| C2 trend pullback SHORT strict | 0 | 0 | — | Rejected: no sample |
| C2 trend pullback SHORT broad | 5 | -1.1462 | 0 | Rejected |
| C3 sweep reversal LONG base | 4 | -0.4583 | 7.2015 | Rejected: sample/expectancy |
| C3 sweep reversal LONG strict | 0 | 0 | — | Rejected: no sample |
| C3 sweep reversal LONG broad | 6 | +0.1233 | 21.5720 | Rejected: sample/expectancy |
| C4 sweep reversal SHORT base | 23 | +0.0344 | 1.6113 | Rejected: sample/expectancy/stability |
| C4 sweep reversal SHORT strict | 6 | -0.2964 | 0.8765 | Rejected |
| C4 sweep reversal SHORT broad | 28 | -0.1617 | 1.3031 | Rejected |

Cycle 1’s issue was the preregistered family admission density, not a hidden
DecisionQuality bottleneck: 93 family signals were emitted and 90 passed the
authority gate.

## Cycle 2 — targeted breakout and momentum reconstruction

| Candidate | Fills | Expectancy-R | PF | Result |
| --- | ---: | ---: | ---: | --- |
| D1 range breakout LONG base | 414 | -0.0789 | 0.9309 | Rejected |
| D1 range breakout LONG strict | 372 | -0.1034 | 0.7787 | Rejected |
| D1 range breakout LONG broad | 879 | -0.1012 | 0.8573 | Rejected |
| D2 range breakout SHORT base | 409 | -0.1907 | 0.6930 | Rejected |
| D2 range breakout SHORT strict | 334 | -0.1801 | 0.6705 | Rejected |
| D2 range breakout SHORT broad | 764 | -0.1729 | 0.7266 | Rejected |
| D3 momentum expansion LONG base | 1,387 | -0.0779 | 1.0228 | Rejected |
| D3 momentum expansion LONG strict | 526 | +0.0169 | 1.1572 | Rejected: weak/unstable/cost-sensitive |
| D3 momentum expansion LONG broad | 2,146 | -0.0921 | 0.9711 | Rejected |
| D4 momentum expansion SHORT base | 941 | -0.0968 | 1.1319 | Rejected |
| D4 momentum expansion SHORT strict | 313 | -0.1614 | 1.1899 | Rejected |
| D4 momentum expansion SHORT broad | 1,585 | -0.0957 | 1.1412 | Rejected |

The highest-expectancy adequate-sample candidate was strict momentum-expansion
LONG, ID `f2262e97c9019f72`. Its 526 fills formed 372 same-time/direction
clusters (98 correlated clusters). WR was 47.34%; expectancy was only
`+0.016916R`; PF was 1.1572; average MFE/MAE were 0.5055R/0.4926R. Fold
expectancies were `-0.0164R`, `+0.1709R`, `-0.0874R`, and `-0.0170R`. The
block-bootstrap 95% interval was `[-0.1220R, +0.1756R]`. Higher-cost and stress
expectancies fell to `-0.0300R` and `-0.0971R`. XRP expectancy was
`-0.2977R`, further demonstrating cross-market instability.

## Integrity and next capability

All 24 variants remain in machine-readable registries. Every emitted plan
passed `TradePlanIntegrity` and the actual `DecisionQualityEngine`, used
next-open market fills, fixed fees/slippage, 120-bar exits, and conservative
stop-first OHLC semantics. Funding was unavailable and explicitly not modeled.

Artifact reconciliation `21e40bb64acff57a` validates both registries, all
candidate identities, Development-only timestamps, replay authority, entry
policy, code-file hashes, and partition locks.

The next research capability most likely to add orthogonal information is a
point-in-time, integrity-certified derivatives dataset: synchronized funding,
open interest, and perpetual basis first, followed only if reliable by
liquidations and trade-flow/order-book imbalance. A new cycle must be
separately preregistered after that data exists; the current OHLCV budget is
closed.

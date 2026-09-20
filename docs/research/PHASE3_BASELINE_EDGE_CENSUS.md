# Phase 3A Baseline Edge Census

Status: frozen baseline completed once on 2026-09-13. No strategy parameter, threshold, plan geometry, decision rule, runtime admission rule, PAPER path, LIVE path, or copy-execution path was changed for this measurement.

## Frozen identity and data

- Frozen config hash: `84533946dfbbbf65`; Git revision: `91ae7ab69d693331669b48ad5a0882d0999ccceb`; dirty-tree identity: `acb8cc47232c7537`.
- Strategy sources: `Analyzer` `f713a23908224e77`, `TradePlanIntegrity` `0a5818912055de6c`, and `DecisionQualityEngine` `06c956bfb245cbc7`; decision contract `decision-authority-v1`; replay engine `historical-replay-v1`.
- Universe: the first three configured production `WATCHLIST` symbols, fixed before outcomes: `BTC`, `ETH`, and `SOL`. The sole timeframe is the runtime default `1h`.
- Source: immutable, materialized OKX public USDT-swap candles. Each dataset has 999 closed candles from `2026-08-03T01:00:00Z` through `2026-09-13T15:00:00Z` and is `VALID` under the Phase 2A contract.
- Dataset hashes: BTC `f9cf48642923b7ddc92b87215f69f506c2bae2973a961357a7b51fb776f54f4e`; ETH `f6332f031e3ae0a8bb79e8f910815c30cda7c80f7600b2828bca2dda830c8d0d`; SOL `468c32b90725e2aff40e833d426f307093696626a0ce96d332d706706a9c2c8e`.
- Costs: 0.05% entry fee, 0.05% exit fee, and 0.03% MARKET slippage. Funding is explicitly `NOT_MODELED`.
- Artifact: ignored `research_artifacts/phase3/baseline-census-a0828246973815e6.json`; run ID `a0828246973815e6`; file SHA-256 `d985c42857248821a19e064ff5aedd6c25d80cf943859a1c7182dbf7893b1e8f`.

The adapter supplies each eligible closed-candle snapshot to the actual `Analyzer`, its existing `TradePlanIntegrity`, synchronized decision-time `MarketContextEngine` enrichment where BTC context is available, and the actual `DecisionQualityEngine`. Approved plans then use the Phase 2B `HistoricalReplayEngine`. The runner never invokes `SignalRecorder`, Telegram, PAPER, LIVE, copy execution, private APIs, or production persistence. Legacy database-backed probability history and persistent `MarketMemory` are neutralized because Phase 2B classified those records as unreplay-certified; that is an explicit limitation, not reconstructed evidence.

## Decision census and global baseline

The 220-candle warmup is feature-only. Evaluation decisions begin at `2026-08-12T05:00:00Z`, occur once per completed 1h candle, and end at `2026-09-13T15:00:00Z`.

| Measure | Frozen result |
| --- | ---: |
| Eligible decisions | 2,337 |
| APPROVED | 321 (13.74%) |
| NO_TRADE | 2,016 (86.26%) |
| Simulated fills | 247 |
| Planned entries expired unfilled | 74 |
| Wins / losses / breakevens | 148 / 99 / 0 |
| Win rate | 59.92% |
| Gross price-unit PnL | 12,419.29 |
| Modeled fees | 7,112.21 |
| Net price-unit PnL | 5,307.07 |
| Net expectancy per fill | 21.4861 price units |
| Expectancy-R | 0.1762 R |
| Average win / loss | 213.43 / -265.46 price units |
| Payoff ratio | 0.8040 |
| Profit factor | 1.2019 |
| Maximum drawdown | 10,405.17 price units |
| Average MFE / MAE | 0.5746 R / 0.3013 R |
| Average holding time | 8.15 bars (hours) |
| Maximum consecutive wins / losses | 43 / 11 |

Price-unit PnL, fees, expectancy, and drawdown are not cross-symbol capital performance: one price unit has different economic value for BTC, ETH, and SOL. Expectancy-R is the comparable risk-normalized measure. The published maximum drawdown also follows artifact/run order and is not a portfolio-equity claim. Sharpe and Sortino are intentionally omitted because the sample is a short, non-capital-normalized replay.

## Attribution census

All decisions are attributed to frozen strategy version `84533946dfbbbf65` and family `UNIFIED_ANALYZER_MIXED`. The current architecture does not expose a causal dominant sub-strategy or stable vote ledger, so finer strategy contribution remains `MIXED/UNKNOWN` rather than being invented.

### Symbol

| Symbol | Decisions | Approved | Fills | WR | Expectancy | Expectancy-R | PF | Net PnL | Avg MFE / MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BTC | 779 | 117 | 90 | 55.56% | 45.0912 | 0.0797 R | 1.1576 | 4,058.21 | 0.5397 / 0.2425 R |
| ETH | 779 | 102 | 79 | 74.68% | 15.6724 | 0.4969 R | 3.5753 | 1,238.12 | 0.6766 / 0.2635 R |
| SOL | 779 | 102 | 78 | 50.00% | 0.1377 | -0.0371 R | 1.1984 | 10.74 | 0.5116 / 0.4073 R |

ETH is the strongest observed symbol cohort and exceeds the 30-fill comparability floor, but it still covers only 41 days. SOL is approximately flat in price units and negative in R. BTC's positive price-unit contribution must not be compared directly with lower-priced symbols.

### Timeframe and direction

The only timeframe is `1h`; therefore its results are the global results above and no cross-timeframe conclusion is possible.

| Direction | Eligible | Approved | Fills | WR | Expectancy | Expectancy-R | PF | Net PnL | Avg MFE / MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| LONG | 1,877 | 292 | 227 | 62.11% | 38.5756 | 0.2336 R | 1.3972 | 8,756.67 | 0.5830 / 0.3067 R |
| SHORT | 460 | 29 | 20 | 35.00% | -172.4797 | -0.4748 R | 0.1858 | -3,449.59 | 0.4792 / 0.2397 R |

LONG approval frequency is 15.56%; SHORT approval frequency is 6.30%. SHORT is the largest negative observed contributor, but its 20 fills are below the fixed 30-trade comparison threshold and therefore remain `NEGATIVE_CANDIDATE / INSUFFICIENT_SAMPLE`, not a production-disable decision.

### Decision-time regime

| Regime | Fills | WR | Expectancy | Expectancy-R | PF | Net PnL | Avg MFE / MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRANSITION | 245 | 59.59% | 21.3647 | 0.1702 R | 1.1992 | 5,234.34 | 0.5718 / 0.2972 R |
| TRENDING | 2 | 100.00% | 36.3647 | 0.9177 R | n/a | 72.73 | 0.9201 / 0.8039 R |

The two TRENDING fills are `INSUFFICIENT_SAMPLE`. Because 245 of 247 fills carry `TRANSITION`, the run cannot distinguish regime-specific edge or answer whether chop/trend gating is effective.

### Confidence calibration

| Confidence | Fills | WR | Expectancy | Expectancy-R | PF | Avg MFE / MAE | Classification |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 60–69 | 50 | 50.00% | -69.9433 | -0.0001 R | 0.6604 | 0.5662 / 0.2743 R | NEGATIVE_CANDIDATE |
| 70–79 | 64 | 65.63% | 81.8782 | 0.2846 R | 1.9436 | 0.6362 / 0.2719 R | POSITIVE_CANDIDATE |
| 80+ | 133 | 60.90% | 26.7972 | 0.1904 R | 1.3418 | 0.5481 / 0.3256 R | POSITIVE_CANDIDATE, weaker than 70–79 |

All aggregate buckets clear the 30-fill floor, but confidence is not monotonic: 80+ underperforms 70–79 on WR, expectancy, expectancy-R, PF, MFE, and MAE. The earlier short summary accidentally quoted BTC-only bucket counts; the table above is reconciled across all 247 fills in the immutable artifact. This is evidence of calibration weakness, not authorization to change thresholds in Phase 3A.

### Exit reasons and excursion diagnostics

| Exit | N | Net PnL | Expectancy-R | Avg MFE / MAE | Avg holding bars |
| --- | ---: | ---: | ---: | ---: | ---: |
| TP | 148 | 31,587.58 | 1.0084 R | 0.6746 / 0.1723 R | 7.43 |
| STOP | 99 | -26,280.51 | -1.0678 R | 0.4251 / 0.4941 R | 9.23 |

Winners have lower average MAE than losses and reach exit sooner. Losses still average 0.4251 R of prior favorable excursion, which supports an exit/profit-protection hypothesis for testing. MFE/MAE exclude pre-fill, post-exit, and unknowable exit-bar extrema under the Phase 2B contract; consequently some immediate TP winners report zero pre-exit MFE.

## Loss and win forensics

The 99 losses reconcile exactly to these mutually exclusive primary labels:

| Loss classification | N | Post-exit recoveries | Avg MFE / MAE | Net PnL |
| --- | ---: | ---: | ---: | ---: |
| PRIOR_MFE_LOSS | 30 | 8 | 0.9028 / 0.6900 R | -9,764.91 |
| OTHER | 27 | 3 | 0.4998 / 0.6614 R | -8,353.66 |
| LOW_MOVEMENT_CHOP | 21 | 3 | 0.0090 / 0.0080 R | -4,388.61 |
| IMMEDIATE_ADVERSE | 13 | 4 | 0.1013 / 0.7841 R | -1,693.71 |
| GAP_OR_AMBIGUITY_AFFECTED | 8 | 4 | 0.0000 / 0.0000 R | -2,079.61 |

There are 22 post-exit recoveries among losses. They are diagnostics only and never change realized replay outcomes or MFE. The full artifact contains nine material ambiguity labels: eight losses and one winner.

Of 148 wins, 147 receive the primary `WINNER` label and one is ambiguity-affected. Winners average 0.6746 R pre-exit MFE, 0.1723 R MAE, and 7.43 bars to exit; 141 are LONG, 146 are labelled TRANSITION, and symbol counts are BTC 50, ETH 59, SOL 39. The primary winner set's recorded MFE-capture diagnostic averages 66.02%, but it is conservative because exit-bar extrema are intentionally unavailable. The data cannot truthfully determine whether a target was barely touched or how far price traveled after TP without introducing post-exit information, so those requested win-forensic questions remain unresolved.

## Chronological windows, walk-forward, and stability

No optimization occurs in any window. The frozen configuration is identical throughout.

| Role | Decision-time range (UTC) | Decisions | Approved | Fills | WR | Expectancy | Expectancy-R | PF | Net PnL |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DEVELOPMENT | 2026-08-12 05:00 – 2026-08-28 00:00 | 1,140 | 106 | 66 | 71.21% | 61.1088 | 0.3313 R | 1.9001 | 4,033.18 |
| VALIDATION | 2026-08-28 01:00 – 2026-09-05 08:00 | 600 | 105 | 97 | 61.86% | 27.8775 | 0.3182 R | 1.1917 | 2,704.12 |
| FINAL_TEST | 2026-09-05 09:00 – 2026-09-13 15:00 | 597 | 110 | 84 | 48.81% | -17.0265 | -0.1096 R | 0.8141 | -1,430.22 |

The apparent positive edge survives the validation slice but fails the once-opened final-test slice. That makes the aggregate result `UNSTABLE`; it is not evidence of persistent edge. The 60/20/20 chronology is the only meaningful unseen-window sequence available in this 41-day dataset. It is not enough history for multiple independent market-regime walk-forward folds, monthly/quarterly stability, or decay estimation.

## Evidence classification and Phase 3B hypotheses

Largest negative observations are SHORT fills, the final-test window, the 60–69 confidence bucket, SOL's negative expectancy-R, and losses that first achieved substantial MFE. ETH, LONG, and the 70–79 confidence bucket are provisional positive candidates. Only LONG, ETH, the confidence buckets, and TRANSITION have at least 30 fills; SHORT, TRENDING, every alternate timeframe, causal sub-strategy attribution, and multi-regime stability have insufficient evidence. No group is promoted or disabled.

Phase 3B should test these predeclared hypotheses one at a time against the frozen datasets and comparison contract:

1. Add a candidate-only SHORT admission/regime constraint and require improvement in both expectancy and expectancy-R without collapsing sample size.
2. Test confidence recalibration or bucket-specific admission so reported confidence becomes monotonic; do not tune solely to the 70–79 cohort.
3. Test causal profit-protection/exit variants for prior-MFE losses while preserving the Phase 2B fill, stop, ambiguity, and cost rules.
4. Test regime/selectivity features aimed at low-movement/chop and the final-test deterioration, with validation and final-test gates reported separately.
5. Test SOL-specific evidence as a held-out cohort and cost sensitivity rather than inferring from its near-zero price-unit PnL.

Every candidate must retain at least 30 executed trades, improve net expectancy and expectancy-R, not reduce PF, and provide validation/final-test, time-stability, and cost-sensitivity evidence. Higher WR or lower drawdown alone is insufficient.

## Remaining limitations

This is a single short public-OHLC run, not a live-profitability study. It has no historical funding series, bid/ask history, order-book path, actual fills, latency, leverage/liquidation model, capital-normalized sizing, or certified legacy probability/memory state. The universe has three symbols and one timeframe, almost all fills share one regime label, sub-strategy causes are mixed, and the final window is negative. The aggregate is mathematically positive under the stated model but is not a claim of live profitability or production readiness.

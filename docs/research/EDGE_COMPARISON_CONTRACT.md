# Phase 3B Edge Comparison Contract

Every candidate is compared with the frozen Phase 3A baseline using the same materialized datasets, cost model, execution semantics, symbol/timeframe universe, chronological windows, and reporting schema.

A candidate is not unambiguously improved because of higher win rate, lower drawdown alone, or fewer trades. It must:

1. Use the frozen dataset hashes, Phase 2B entry/exit chronology, ambiguity policy, and base cost model.
2. Report every eligible decision and preserve `NO_TRADE`, expired-entry, and fill counts; a candidate may not win by silently discarding losses.
3. Have at least 30 executed trades overall and disclose material cohort counts. A large collapse from the 247-fill baseline requires explicit review even when the floor is met.
4. Improve both net expectancy and expectancy-R and not reduce profit factor. For promotion, profit factor is the reconciled `PerformanceAttribution.metrics()` net-PnL PF across the fixed symbol mix; an R-normalized PF may be reported diagnostically but cannot substitute for that gate.
5. Report maximum drawdown, trade count, WR, fees, MFE/MAE, and chronological DEVELOPMENT, VALIDATION, and FINAL_TEST results. WR or drawdown alone is never a promotion gate.
6. Show positive or non-degrading validation and final-test evidence and time stability; aggregate improvement cannot hide a failed unseen window.
7. Repeat the comparison under documented higher-fee/slippage sensitivity. Missing funding remains a limitation and may not be treated as zero realized cost.
8. Record config hash, dataset hashes, replay version, variant count, fold identity, sample counts, cost assumptions, and all failed gates in a deterministic candidate artifact.

`compare_baselines()` is the minimum mechanical guard: it returns `NOT_UNAMBIGUOUSLY_IMPROVED` when the sample, expectancy, expectancy-R, PF, final-test, or stability evidence is missing. Drawdown, sample collapse, cohort stability, and cost sensitivity remain mandatory human/research review fields even when the mechanical guard passes. Promotion criteria must be declared before opening the final-test result and may not be retuned to a candidate's outcome.

This contract is a research gate only. It neither changes runtime admission nor promotes/disables a strategy.

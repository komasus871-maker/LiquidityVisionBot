# Phase 3C Validation

`F1_COUNTERTREND_REVERSAL_LONG` (`c49803034d94792e`) was frozen from development composition evidence before Phase 3C validation. It retained only decisions already approved by DecisionQuality, with unchanged entry, stop, target, cost, and replay semantics.

Validation results: 20 fills, 70% WR, `+0.451803R` expectancy, net-PnL PF `4.6567`, and maximum drawdown `952.78`. The frozen baseline had 280 fills, `+0.208171R`, net-PnL PF `2.5064`, and drawdown `9,602.79`.

The apparent metric improvement does **not** qualify F1: 20 validation fills are below the pre-registered 30-fill minimum, and the three-asset result includes only 9 BTC, 4 ETH, and 7 SOL fills. Its 24-bar bootstrap is degenerate at this small sample and is not evidence of certainty. F1 is therefore `REJECTED`; no finalist is frozen and `BLIND_HOLDOUT` / `LEGACY_SEEN_TEST` remain untouched.

Development-only internal chronological folds were all positive but small: 11 fills at `+0.604408R`, 16 at `+0.469504R`, and 16 at `+0.796432R`. This does not repair the validation sample deficit.

F1 cost sensitivity is directionally positive but non-qualifying: base/higher/stress expectancy-R was `+0.451803`, `+0.410126`, and `+0.368449`; reconciled net-PnL PF was `4.6567`, `4.2699`, and `3.9173`. These figures are retained only as robustness diagnostics for a rejected 20-fill candidate, not as a reason to lower the sample requirement.

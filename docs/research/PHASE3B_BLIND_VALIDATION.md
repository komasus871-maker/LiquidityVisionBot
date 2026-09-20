# Phase 3B Blind Validation Record

Status: **not accessed.**

`BLIND_HOLDOUT` is protected by the Phase 3B access guard. It may be evaluated only if the frozen validation candidate passes every pre-registered gate: at least 30 OOS fills, positive aggregate OOS expectancy-R, no catastrophic fold, non-degrading PF, acceptable drawdown relative to baseline, fixed-scenario cost robustness, and no Phase 1A/1B/2A/2B regression.

Validation rejected the sole frozen roster candidate, `H2_CONFIDENCE_CEILING_70` (`ab5f8d32fd4b51a7`): while its validation expectancy-R was positive and exceeded the baseline, its reconciled base-cost net-PnL PF was `2.228423090188`, below the baseline's `2.506395574073`. This fails the pre-registered non-degrading-PF promotion gate. No finalist exists.

No blind metrics, decisions, outcomes, or legacy-seen metrics were accessed. The resulting Phase 3B outcome is `NO CANDIDATE QUALIFIED FOR BLIND VALIDATION`; no blind or legacy result will be opened.

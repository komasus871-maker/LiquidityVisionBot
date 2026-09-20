# Phase 3C Edge Decomposition

## Truthful Analyzer composition

Phase 3C records only real decision-time `score_components` emitted by Analyzer. The usable causal components are trend, structure, liquidity/SMC, momentum, entry, and risk labels, plus direction, readiness, confidence, plan geometry, and existing market-regime fields. The current artifact does not expose a separate numeric bullish/bearish vote, a standalone reversal score, or a distinct volatility strategy score; those are `UNKNOWN`, not reconstructed.

The development classifier is exhaustive and outcome-free. `COUNTERTREND_REVERSAL_LONG` requires existing `Trend conflicts` and `CHOCH confirmation` labels with a LONG direction. Its SHORT sibling is separately retained. `TREND_STRUCTURE_CONTINUATION` requires aligned trend, structural trigger, BOS/CHOCH/displacement confirmation, and no trend/structure/momentum conflict. Other conflicting evidence is `CONFLICTED_EVIDENCE`; the remainder is `MIXED_UNCLASSIFIED` or `UNKNOWN`.

## Development family baseline

| Family | Eligible | Approved | Fills | Expectancy-R | Net-PnL PF | Interpretation |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Countertrend reversal LONG | 742 | 57 | 43 | +0.625662 | 3.6265 | Only development-positive family with adequate sample. |
| Countertrend reversal SHORT | 206 | 12 | 11 | -0.406148 | 0.3942 | Insufficient sample; no SHORT rule. |
| Trend/structure continuation | 1,617 | 246 | 192 | -0.283763 | 0.4754 | Negative despite aligned evidence. |
| Conflicted evidence | 6,639 | 369 | 280 | -0.138972 | 0.9253 | Broad, mixed, negative cohort. |
| Mixed/unclassified | 2,847 | 282 | 215 | -0.258099 | 0.8579 | Negative residual. |

Families reconcile exactly to the 12,051 development decisions and 741 fills. The countertrend LONG cohort had lower average MAE (`0.3257R`) and higher average MFE (`0.6277R`) than the broad baseline, while the weak continuation and mixed cohorts point to entry/composition failure rather than a single global exit issue.

## Contradictions and exposure

`Trend conflicts` plus `CHOCH confirmation` is a genuine current-code contradiction/resolution pattern: the structural CHOCH supports a reversal while the trend evidence opposes it. It is not a manufactured label. In development, LONG examples were positive; the 11 SHORT examples were not. The strong conditional result is a hypothesis, not proof of causality.

Of 741 development fills, 408 belong to 185 same-time, same-direction clusters; maximum cluster size is three. BTC/ETH/SOL exposure is therefore correlated and headline trade count overstates independent observations. No portfolio behavior was changed.

## Confidence, entry, and exit diagnostics

Raw confidence remains non-monotone overall: development 60–69 had 224 fills and `-0.005413R`, while 70–79 had 266 and `-0.162571R`, 80–89 had 118 and `-0.350846R`, and 100 had 59 and `-0.379403R`. The Phase 3B confidence ceiling remains rejected; Phase 3C did not fit family-specific calibration because the only family candidate was already too small in validation.

Nearly all development fills (739/741) were existing `PLANNED_ZONE` limits. Their average hold was 9.58 bars, with 439 stop and 300 TP exits; the two `MARKET_READY` fills are insufficient for a conclusion. There were no filled-trade timeouts. Countertrend-reversal LONG had 9.30 average bars, 34 TP versus 9 stop exits, `0.6277R` average MFE, and `0.3257R` average MAE. This is descriptive; it does not justify a family exit change.

The continuation and mixed families had lower MFE (`0.5058R` / `0.5291R`) and negative expectancy, whereas the F1 family had stronger favorable excursion. This supports examining family composition before global exit surgery, but no exit variant was created or tested.

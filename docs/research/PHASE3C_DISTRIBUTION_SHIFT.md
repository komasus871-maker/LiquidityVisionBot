# Phase 3C Development–Validation Distribution Shift

This report compares decision-time evidence only. It is descriptive, not causal, and does not use blind or legacy data.

The frozen Analyzer produced 12,051 eligible DEVELOPMENT decisions (966 approved, 741 fills) and 4,383 VALIDATION decisions (382 approved, 280 fills). Development was net negative while validation was positive, so aggregate results should not be interpreted as a stable global edge.

| Decision-time measure | Development | Validation | Observation |
| --- | ---: | ---: | --- |
| LONG / SHORT decisions | 5,881 / 6,170 | 3,060 / 1,323 | Validation is materially more LONG-heavy. |
| Approval rate | 8.02% | 8.71% | Modestly higher validation admission. |
| Mean confidence | 65.96 | 67.21 | Small score shift. |
| Mean absolute directional edge | 43.30 | 45.65 | Modestly stronger directional separation. |
| Mean readiness | 51.90 | 51.98 | Essentially unchanged. |
| Proposed RR | 3.0 | 3.0 | Geometry is unchanged. |
| Countertrend reversal LONG / SHORT | 742 / 206 | 167 / 122 | Validation has a much larger SHORT share in this family. |
| Trend/structure continuation | 1,617 | 532 | Similar relative prevalence. |
| Conflicted evidence | 6,639 | 2,549 | Still the dominant composition. |

The observable composition is materially mixed: `Trend aligned` appeared in 674 development fills, while only 67 carried `Trend conflicts`; `Structure trigger is present` appeared in 374 fills, `Momentum conflicts` in 167, and `No aligned structural trigger` in 191. Most approved fills were labelled transitional by regime, but that label was already blocked as an admission dimension in Phase 3B.

The concrete distribution-shift artifact records direction, score/confidence, directional edge, readiness, structure/CHOCH/displacement labels, component-label counts, symbol mix, approval rate, entry type, plan RR, and family counts for DEVELOPMENT and VALIDATION. It uses no outcome field in the feature rows. The notable research question is therefore not “which label caused a win,” but whether the same causal compositions occur at different frequencies and have different conditional outcomes across chronological periods.

The Phase 3C candidate is registered only after DEVELOPMENT composition analysis in [PHASE3C_STRATEGY_HYPOTHESES.md](PHASE3C_STRATEGY_HYPOTHESES.md); this report is not used to retune it after validation.

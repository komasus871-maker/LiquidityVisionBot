# Phase 3C Strategy-Family Hypotheses

Status: registered from DEVELOPMENT-only Analyzer composition evidence before any Phase 3C candidate validation result is inspected.

## Frozen evidence and boundaries

Phase 3A parent `84533946dfbbbf65`, Phase 3B datasets/hashes, costs, replay engine, and temporal boundaries are immutable. Phase 3B's rejected `H2_CONFIDENCE_CEILING_70` remains rejected. `BLIND_HOLDOUT` and `LEGACY_SEEN_TEST` remain unavailable.

Phase 3C uses only Analyzer fields already present at `T_decision`: direction and the exact `score_components` labels. It neither changes Analyzer scoring nor DecisionQuality authority. A candidate can only retain a decision already marked `APPROVED` by DecisionQuality; it cannot synthesize a plan, entry, stop, target, or execution result.

## Family map

The exhaustive, causal classifier is deliberately small:

| Family | Existing required evidence |
| --- | --- |
| `COUNTERTREND_REVERSAL_LONG` | `Trend conflicts` plus `CHOCH confirmation`, direction LONG |
| `COUNTERTREND_REVERSAL_SHORT` | Same evidence, direction SHORT |
| `TREND_STRUCTURE_CONTINUATION` | Trend aligned, structure trigger, BOS/CHOCH/displacement confirmation, and no trend/structure/momentum conflict |
| `CONFLICTED_EVIDENCE` | Any existing trend, structure, or momentum conflict not classified above |
| `MIXED_UNCLASSIFIED` / `UNKNOWN` | Remaining labels / no attributable labels |

The classifier deliberately preserves `UNKNOWN`; it has no outcome input.

## One frozen Phase 3C candidate

| Candidate ID | Family | Rationale | Rule |
| --- | --- | --- | --- |
| `F1_COUNTERTREND_REVERSAL_LONG` | `COUNTERTREND_REVERSAL_LONG` | DEVELOPMENT contained 43 filled examples with `+0.625662R` and PF `3.9551`; the market rationale is a structural CHOCH reversal against the currently detected trend. | Retain only already-APPROVED decisions in this family; preserve existing entry, stop, target, costs, and replay behavior. |

This is a research-family reconstruction, not a global “reject SHORT” rule: `COUNTERTREND_REVERSAL_SHORT` has only 11 development fills and is explicitly `INSUFFICIENT_SAMPLE`. No SHORT rule is tested or changed.

## Promotion contract, declared before Phase 3C validation

The sole candidate must have at least 30 validation fills, positive validation expectancy-R, reconciled net-PnL PF no lower than baseline, no unacceptable drawdown deterioration, positive/non-catastrophic chronological folds, fixed higher/stress-cost resilience, and an interpretable executable rule. Its sharp reduction in trade count requires explicit review; positive WR or lower drawdown alone cannot qualify it. At most one candidate may become a finalist. If it fails any gate, no blind or legacy access is permitted.

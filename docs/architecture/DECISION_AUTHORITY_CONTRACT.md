# Decision Authority Contract

## Boundary

`decision-authority-v1` defines the only production semantic boundary from analysis/observation to a trade-capable signal.

- **Analysis** is raw `Analyzer` output, including directional evidence and the `TradePlanIntegrity` result.
- **Observation** is storable/displayable market information. It may be weak, vetoed, NO_TRADE, or advisory and has no execution authority.
- **Approved trade signal** is a LONG or SHORT decision explicitly approved by `DecisionQualityEngine`, accepted by `SignalRecorder`, and persisted with intact provenance.

## Authoritative stack

1. `Analyzer.analyze` produces direction, scores, status, evidence, and plan inputs. `TradePlanIntegrity` owns plan geometry inside this analysis.
2. `ProbabilityEngine.enrich` adds historical context. It does not authorize a trade.
3. `DecisionQualityEngine.enrich` is the sole semantic admission/veto owner. Its unchanged gates cover executable status, direction/setup score, hard blockers, regime, and `plan_valid`; Phase 1B also rejects non-LONG/SHORT and explicit INVALID/STALE/INCOMPLETE data-quality states.
4. `ConvictionEngine`, `UnifiedDecisionEngine`, and `DecisionBrain` remain reporting/advisory read models. They cannot override `decision_outcome`.

An accepted decision must contain the exact authority/version, a non-empty source, a path ending in `DecisionQualityEngine`, a timestamp, `decision_gate_passed=True`, `decision_outcome=APPROVED`, and LONG/SHORT direction. Missing, malformed, unsupported, or contradictory metadata fails closed.

## Outcomes and vetoes

- `APPROVED`: eligible to be offered to `SignalRecorder`; recorder lifecycle/risk/persistence checks may still reject it.
- `NO_TRADE`: valid terminal semantic outcome for neutral direction, weak/incomplete evidence, veto, invalid plan, hostile regime, or explicit bad data quality. It remains observable but cannot be promoted.
- `decision_veto_reasons`: deterministic machine-readable reasons explaining why admission failed.

No fallback LONG/SHORT, synthetic zero-confidence trade, AI flag, research ranking, ACTIVE status, or complete-looking price geometry is an approval substitute.

## Allowed promotion path

```text
Analyzer -> TradePlanIntegrity -> ProbabilityEngine -> DecisionQualityEngine
    -> APPROVED -> SignalRecorder -> SignalHistory
    -> CopyExecutionPlanner -> ExecutionValidator -> PAPER or LIVE/COPY lifecycle

    -> NO_TRADE -> ObservationHistory only
```

`SignalRecorder` records an attempted promotion as an observation with `promotion_admission` diagnostics before returning NO_TRADE/rejected candidates. Approved provenance is copied into `SignalHistory.features_json`; `ExecutionValidator` rechecks it mode-independently.

## Prohibited bypasses

- Calling `SignalRecorder` with raw/partial Analyzer, watch, scanner, alert, AI, research, or EdgeDiscovery output.
- Treating `status=ACTIVE`, direction, price geometry, confidence, or a user-visible recommendation as authority.
- Creating PAPER or LIVE/copy plans from signals lacking valid persisted Phase 1B provenance.
- Allowing advisory decision/read-model output to replace `DecisionQualityEngine` admission.
- Automatically backfilling historical approval that cannot be proven.

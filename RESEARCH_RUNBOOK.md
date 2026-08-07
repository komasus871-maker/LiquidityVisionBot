# Research Operations Runbook

## Safety contract

Research is read/projection-only. It must never size a trade, admit risk, submit an order, mutate a position, alter portfolio accounting, bypass deterministic policy, enable LIVE, or enable `AI_GATED`.

## Recommended production configuration

```text
RESEARCH_INTERVAL_SECONDS=300
RESEARCH_BATCH_LIMIT=200
EDGE_MIN_SAMPLES=20
EDGE_MODERATE_SAMPLES=50
EDGE_HIGH_SAMPLES=100
EDGE_BOOTSTRAP_SAMPLES=500
EDGE_BOOTSTRAP_MAX_N=500
EDGE_COMBINATION_MIN_SAMPLES=20
EDGE_COMBINATION_MIN_SUPPORT=0.05
EDGE_MAX_COMBINATIONS=120
EDGE_MAX_NEW_HYPOTHESES=10
EDGE_FORWARD_MIN_SAMPLES=30
EDGE_HYPOTHESIS_STALE_DAYS=90
EDGE_WALK_FORWARD_MIN_TRAIN=60
EDGE_WALK_FORWARD_VALIDATION_SIZE=20
EDGE_HISTORY_LIMIT=5000
EDGE_AI_COMPARISON_LIMIT=500
EDGE_RANK_OVERLAP_MINUTES=60
RESEARCH_ESTIMATED_EXECUTION_COST_PCT=0.19
SCALPING_MIN_SAMPLES=100
SCALPING_MIN_GROSS_COST_MULTIPLE=1.5
```

Increase bootstrap counts or history limits only after measuring worker latency and PostgreSQL load. Do not lower sample gates to manufacture findings.

## Deployment verification

1. Deploy with existing PAPER/LIVE and AI mode values unchanged.
2. Confirm startup migrations complete and the `research_engine` worker is healthy.
3. Run `/research` and verify snapshots/resolved counts.
4. Run `/edge_discovery`; initial `INSUFFICIENT` is correct when samples are sparse.
5. Run `/feature_edge`, `/hypotheses` and `/forward_tests`.
6. Run `/strategy_lab`, `/regimes`, `/signal_rankings` and `/scalping_research`.
7. Confirm copy diagnostics, positions and portfolio reconciliation remain consistent.

## PASS indicators

- Worker cycles are bounded and restart without duplicate vectors/findings/evaluations.
- Only `TRUSTWORTHY_DECISION_TIME` rows count toward evidence.
- Late, reconstructed, incomplete and contaminated rows are visible in quality counts but excluded.
- Hypothesis filters and cutoffs remain unchanged between evaluations.
- Forward sample timestamps are later than the discovery cutoff.
- Rankings and strategy recommendations state that execution authority is false.
- Research failures are logged in worker telemetry without stopping copy execution.

## Expected non-failures

- `INSUFFICIENT`, `VERY_LOW`, empty hypotheses and unavailable exit policies are valid early-production results.
- A disappearing out-of-sample edge is a successful rejection, not a system failure.
- Missing BTC beta/sector analytics are intentional until safe immutable source data exists.

## Incident response

If research load is excessive, increase `RESEARCH_INTERVAL_SECONDS` or reduce `RESEARCH_BATCH_LIMIT` and `EDGE_HISTORY_LIMIT`. Existing copy positions and workers continue independently. Do not delete research tables during an incident; older code safely ignores the additive schema.

If data contamination is reported, stop interpreting findings, preserve rows for audit, identify the snapshot producer, and resume only after new clean forward snapshots accumulate.

## Export

`python tools/export_alpha_dataset.py --output exports/alpha_dataset.csv`

Exports identify `decision_features_json` separately from `later_outcome_json`, include feature and data-quality versions, and never read mutable current signal features when an immutable snapshot exists.

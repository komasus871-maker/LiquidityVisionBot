# Performance Metric Contract

All `PerformanceAttribution` aggregates use one explicit evaluation mode and reconcile exactly to their supplied outcome ledger rows. Each result exposes executed sample size, decision date range, dataset identities, and the distinct cost assumptions present in its rows.

| Metric | Definition |
|---|---|
| Trade count | Outcomes with a simulated fill; expired entries are excluded. |
| Win/loss/breakeven | Net PnL greater than, less than, or equal to zero. |
| Gross / net PnL | Price movement before costs / gross minus fees and modeled funding. |
| Expectancy | Mean net PnL per executed outcome; `expectancy_r` is mean net R. |
| Profit factor | Sum positive net PnL divided by absolute sum negative net PnL; `null` if there is no loss denominator. |
| Payoff ratio | Average winning PnL divided by absolute average losing PnL. |
| Drawdown | Largest peak-to-trough decline in the chronological net-PnL equity sequence. |
| MFE / MAE | Mean per-outcome favorable/adverse R while active, excluding ambiguous pre-fill and post-exit movement. |
| Consecutive wins/losses | Longest chronological run of positive/negative net outcomes. |

The ledger carries decision/entry/fill/exit timestamps, intended/simulated/actual fills, stop/target, gross/net PnL, fees, funding status, R, MFE/MAE, ambiguity flags, provider/data-quality provenance, dataset hash/range, strategy/config engine identity, and evaluation mode. Attribution supports strategy version, symbol, timeframe, direction, regime, confidence/probability/RR buckets, UTC hour/session, data quality/provider, decision source, and exit reason.

Sharpe, Sortino, recovery factor, and exposure are intentionally not reported by the new replay aggregate: no statistically meaningful annualization or compatible return/capital series is available yet. Small cohorts must remain explicitly low-sample research, not edge claims.

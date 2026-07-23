# Liquidity Vision Intelligence v9.7.0

## Strategy Genome & Similar Trade Intelligence

This release turns each copy-execution attempt into a deterministic historical feature snapshot and makes that context searchable.

### Runtime integration

- `CopyTradingService` snapshots the genome before the execution decision is persisted.
- Accepted and rejected attempts store `genome_json` and `genome_fingerprint`.
- Similarity search reads only terminal executed positions and terminal shadow outcomes.
- Open positions are excluded to avoid outcome leakage.
- `/copy_similar` uses the latest copy attempt; `/copy_similar <signal_id>` analyzes a specific signal.

### Metrics

The report returns the number of sufficiently similar resolved trades, win rate, average R, average MFE, average MAE, genome fingerprint, similarity score and Replay signal IDs.

### Safety

Similarity is diagnostic in v9.7.0. It does not weaken execution guardrails, alter equity, or enable LIVE execution.

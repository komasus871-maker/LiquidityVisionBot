# Liquidity Vision Intelligence v9.8.0

## Explainable Similarity Intelligence

v9.8.0 turns Strategy Genome search into an explainable intelligence layer.

### Runtime behavior

- `/copy_similar` analyzes the latest copy attempt.
- `/copy_similar <signal_id>` analyzes a selected signal.
- `/genome` displays the latest normalized execution context.
- `/genome <signal_id>` displays a selected signal Genome.

Similarity is decomposed into Structure, Liquidity, Market, Indicators, and Execution. Each Replay exposes its strongest matching features and largest contextual differences. Aggregate statistics use every qualifying resolved Genome, while Telegram output is limited to the closest Replays.

### Statistical reliability

The report labels confidence as VERY LOW, LOW, MEDIUM, or HIGH using both sample size and average similarity. This label describes reliability of the historical estimate; it never changes risk limits or authorizes execution.

### Data integrity

Only CLOSED paper executions and REJECTED attempts with terminal shadow outcomes are included. Open outcomes are excluded to prevent leakage. LIVE execution remains fail-closed.

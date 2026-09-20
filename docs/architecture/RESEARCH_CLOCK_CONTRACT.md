# Research Clock Contract

Phase 2B defines the minimum chronology required for a historical performance claim. It applies to `HistoricalReplayEngine` in `services/research_replay.py`; it does not alter trading decisions, execution, or existing signal records.

## Time semantics

For a canonical timeframe duration `D` and candle open timestamp `T_open`:

```text
candle interval:          [T_open, T_open + D)
closed OHLCV available:   T_close = T_open + D
decision time:            T_decision >= T_close
entry eligibility:        T_entry_eligible >= T_decision
simulated/actual fill:    T_fill >= T_entry_eligible
exit:                     T_exit >= T_fill
```

The engine uses `utils.timeframe` for canonical duration resolution and `DataIntegrityEngine.prepare_market_frame(... require_time_integrity=True)` for UTC timestamps, closed candles, ordering, duplicates, geometry, spacing, gaps, finite OHLCV, and future-time rejection. Historical age is not evaluated against wall-clock freshness: the final evaluated candle close is the reference time.

The decision callback receives only `frame[:decision_index + 1]`, where the last frame is closed. Later candles are unavailable to strategy calculation. Warmup is permitted as preceding input only; it never enters the unseen evaluation period.

## Splits and walk-forward

`TemporalSplit` is chronological: development records have `T_decision <= development_end`; unseen test records begin at `test_start`. Random temporal shuffling is forbidden. `walk_forward_windows` creates non-overlapping test windows after a frozen train/development window. A Phase 3 experiment must record the strategy/config version, dataset identity/range, split/window, engine version, costs, and variant count before interpreting out-of-sample results.

Existing `ResearchEngine` and `EdgeDiscoveryEngine` preserve immutable signal snapshots and chronological snapshot dates, but their outcome records do not contain decision-time OHLC streams or replay fill chronology. They remain research observations, not historical-replay proof.

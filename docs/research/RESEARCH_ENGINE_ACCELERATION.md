# Exact Research Replay Acceleration

## Status

`PARITY_CERTIFIED_FOR_FROZEN_F1_REPLAY` on representative early-history,
late-history, and cross-symbol BTC-context slices. This is an engineering
optimization only. It does not change Analyzer thresholds, F1 identity,
DecisionQuality admission, replay clocks, costs, split boundaries, production
defaults, or protected-data access.

## Profiled root cause

A 24-decision canonical BTC slice required 8.866 profiled seconds and
22,246,367 calls. `FVG.analyze` consumed 5.857 seconds: every expanding prefix
was scanned repeatedly with Pandas `iloc`, and `active`/`nearest` regenerated
the same complete gap lists. Breaker, mitigation, and order-block scans added
more row-wise Pandas overhead. Frozen-plan cost replays also constructed every
unused expanding DataFrame even though only a sparse timestamp map could
produce a plan. Finally, a complete immutable dataset was validated once by
`HistoricalReplayEngine` and then copied and revalidated again for every
decision.

The growth pattern was effectively quadratic in history length. ETH and SOL
also repeated synchronized BTC analysis, magnifying the cost.

## Implemented exact optimizations

- Vectorized the existing FVG, breaker, order-block, mitigation-block, and
  confirmed-swing predicates. Ordering, tie-breaking, bounds, labels, and
  causal prefix availability remain unchanged.
- Added `HistoricalReplayEngine.run_plans` for immutable timestamp-keyed plans.
  It preserves preparation, run identity, chronology, `_simulate`, costs, and
  provenance while avoiding unused prefix allocation.
- Added an explicit prevalidated-research pipeline. It is available only when
  an offline runner has already validated and normalized the complete immutable
  dataset. The normal Analyzer default still executes the full fail-closed
  market-data contract.
- Added a once-per-dataset causal feature stream for the exact EMA, RSI, MACD,
  ATR, regime, structure, sweep, FVG, premium/discount, volume, and
  displacement values used at each prefix. Full-history feature work is no
  longer repeated at every decision.
- Removed the offline replay module's eager import of the optional OKX network
  transport. Public fetching still imports the provider at the point of use.

## Exact parity evidence

The frozen economic signature includes decision timestamps, direction,
confidence, quality, entries, stops, targets, RR, DecisionQuality outcome and
vetoes, Analyzer attribution, family identity, fills, exits, MFE/MAE, fees,
and net outcomes.

| Representative comparison | Result |
| --- | --- |
| BTC 24-decision slice containing approved baseline and F1 fills | Exact SHA-256 `88dcfd8a850cfb94e01b42ea24c264d0aa1ec76227eec612a02241e6949f1a02` before and after |
| BTC late-history 24-decision slice vs frozen checkpoint | Exact decision parity |
| ETH late-history 24-decision slice with synchronized BTC context vs frozen checkpoint | Exact decision parity; SHA-256 `76352592be1949d4aecb7ca3655c63451e5cb0232ab63dac3fc0f4bb1cce9ebc` |
| Complete BTC confirmation: 8,106 decisions / 908 baseline outcomes / 83 F1 outcomes | Exact serialized parity to frozen checkpoint |
| Sparse timestamp-plan replay vs canonical decision factory | Exact serialized `ReplayOutcome` parity |
| Optimized pattern detectors vs legacy implementation across deterministic prefixes | Exact parity |
| Prevalidated research pipeline vs normal Analyzer pipeline | Exact equality for every economic feature and plan field asserted by the test |

Focused verification: `19 passed` across acceleration, MASTER F1, and Phase 2B
truth tests.

## Measured speed

On the same 24-decision BTC slice containing seven baseline outcomes and four
F1 outcomes:

- canonical wall time before changes: `6.4924s`;
- parity-certified wall time after changes: `0.3064s`;
- observed speedup: `21.19x`;
- decision/outcome signature: unchanged.

A late-history slice, where prefixes are about 8,700 candles long, completed in
`1.5407s` for 24 BTC decisions and `1.4495s` for 24 ETH decisions with BTC
context before the causal stream was enabled. The complete optimized BTC
confirmation then processed all 8,106 decisions, 908 baseline outcomes, and 83
F1 outcomes in repeated runs of `21.43s` to `22.36s`. The reproducible
benchmark run took `21.85s` with a `288.156 MB` peak working set. The original
canonical BTC checkpoint took about 2h04m37s, an end-to-end speedup of
approximately `342x`; its decision,
baseline-outcome, and F1-outcome serialized hashes all reconcile exactly.

These timings are engineering measurements, not trading-performance results.
A complete three-symbol optimized runtime measurement remains pending.

## Authority boundary

The optimized utilities implement the same predicates used by production, and
their legacy parity is automated. Production Analyzer calls do **not** opt into
the prevalidated-research shortcut. The sparse-plan API is side-effect-free and
has no order, PAPER, LIVE, Telegram, persistence, or copy-trading authority.

# Phase 3A Frozen Baseline Configuration

The Phase 3A baseline is a measurement of the current tree, not a new strategy version. `FrozenBaselineConfig.capture()` records the Git revision, dirty-tree identity, source digests for `Analyzer`, `TradePlanIntegrity`, and `DecisionQualityEngine`, their relevant constants, configured fixed universe, timeframe, replay-cost configuration, and decision-authority version. Its canonical JSON hash is the baseline configuration identity.

The completed frozen snapshot is:

| Identity field | Value |
| --- | --- |
| Config hash | `84533946dfbbbf65` |
| Git revision | `91ae7ab69d693331669b48ad5a0882d0999ccceb` |
| Working-tree identity | `acb8cc47232c7537` |
| Analyzer source | `f713a23908224e77` |
| TradePlanIntegrity source | `0a5818912055de6c` |
| DecisionQualityEngine source | `06c956bfb245cbc7` |
| Decision authority | `DecisionQualityEngine` / `decision-authority-v1` |
| Replay engine | `historical-replay-v1` |
| Analyzer constants | `MIN_RR=1.35`; `EDGE_NEUTRAL=6.0` |
| Trade-plan geometry | existing runtime entry; `1R/2R/3R` targets |
| Warmup / entry expiry / maximum hold | 220 / 24 / 120 bars |
| Entry / intrabar policy | existing plan selects next-open market or post-decision limit; conservative stop-first ambiguity |
| Costs | 0.05% per-side fee; 0.03% MARKET slippage; funding `NOT_MODELED` |

The predeclared initial evaluation universe is `BTC`, `ETH`, and `SOL`: the first three entries in the configured production `WATCHLIST`, in that order, at the runtime default `1h` timeframe. This is a bounded representative rule fixed before outcomes, not profitability selection. The initial public retrieval is the latest 1,000 closed 1h OKX USDT-swap candles per symbol. The chronological split is 60% development, 20% validation, and 20% final test; it is descriptive for this frozen baseline and must not be tuned against.

`Analyzer`, its existing `TradePlanIntegrity`, `MarketContextEngine` (when synchronized BTC candles are supplied), and `DecisionQualityEngine` run unmodified. `ProbabilityEngine`'s existing database history and `MarketMemory` JSONL are excluded from replay because Phase 2B classified local historical outcomes as unreplay-certified; their inclusion would contaminate a historical baseline with unrelated runtime state. This limitation is recorded in every artifact.

The snapshot is not a claim that every imported feature module was individually content-addressed. The Git revision anchors committed dependencies, the dirty-tree and three strategy-source identities anchor the measured reconstruction state, and the immutable census artifact records the resulting decisions and outcomes. Reproduction must verify these identities and the dataset hashes before comparison; a mismatch creates a new candidate identity and must never overwrite this baseline.

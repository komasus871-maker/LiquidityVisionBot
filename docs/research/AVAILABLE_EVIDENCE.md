# Available Evidence

Local inspection was read-only. `data/database.db` contains zero `signals` and `signal_events`, no `research_signal_snapshots`, no `research_outcomes`, and no `live_execution_fills`. It contains 11 PAPER position/fill rows, but local origin, coverage, decision-time provenance, and whether they are test artifacts cannot be established from this checkout.

Therefore no local record can support a historical win-rate, PnL, expectancy, profit-factor, or LIVE-performance diagnosis. Existing test fixtures are synthetic. `ResearchEngine` snapshots/outcomes can be useful only after a production export is independently classified and linked to decision-time candle data; conceptual signal `realized_r`, PAPER ledger PnL, and LIVE fill evidence must remain separate modes.

## Existing path and leakage classification

| Path | Price/outcome basis | Classification | Use for profitability claims |
|---|---|---|---|
| `core/backtest_engine.py` | Strategy receives an expanding snapshot and fills no earlier than the next row; legacy R-cost/close semantics omit a formal UTC clock and full provenance. | SAFE snapshot boundary; POTENTIAL_LEAKAGE if reused with unvalidated or non-closed rows | No; test-only legacy utility. |
| `core/replay_engine.py` | Replays supplied history through callbacks without execution chronology. | UNKNOWN without caller-specific validation | No. |
| `services/research_engine.py` | Immutable signal snapshot plus later conceptual/observed outcome. | LABEL_ONLY for terminal outcome fields | Only as an observation after external provenance validation. |
| `services/edge_discovery.py` | Chronological snapshot cohorts; outcome keys are forbidden as features. | SAFE for its explicit feature boundary; inputs remain legacy/unverifiable | No historical-execution claim. |
| signal tracking MFE/MAE | Subsequent observed prices around a conceptual signal, not a proven fill lifecycle. | LABEL_ONLY / POTENTIAL_LEAKAGE if interpreted as executed-trade excursion | No executed-performance claim. |
| PAPER ledgers/analytics | PAPER lifecycle fills and ledger PnL. | SAFE only within explicit PAPER mode | PAPER-only after origin/deduplication validation. |
| LIVE reconciliation | Actual/reconstructed exchange execution evidence. | SAFE only within explicit LIVE provenance | LIVE-only; no local fill rows exist. |
| `services/research_replay.py` | Phase 2A-valid closed candles, expanding decision snapshots, post-decision fills, conservative exits and modeled costs. | SAFE under the documented contract | Historical replay only; never PAPER/LIVE. |

Repository searches found no centered rolling windows or negative-shift feature construction in the active research path. The material risks were semantic: legacy inputs can lack candle closure/time integrity; legacy outcomes lack fill chronology; conceptual, PAPER, and LIVE records can otherwise appear comparable; and OHLC bars cannot reveal intrabar event order. Phase 2B contains these risks by validating replay input, separating modes, recording ambiguity, and denying replay authority over runtime execution.

Phase 3A now supplies that historical-replay evidence in the ignored, immutable `research_artifacts/phase3/` datasets and census artifact. It records provider/dataset/config hashes, closed UTC candles, all 2,337 decision snapshots, 321 approved plans, 247 modeled fills, costs, outcomes, and chronological split roles. The resulting aggregate modeled expectancy is positive, but the final-test slice is negative and the sample covers only 41 days, three symbols, and one timeframe. It is therefore an `UNSTABLE` historical baseline for Phase 3B comparison, not evidence of LIVE profitability or production readiness. The full limitations and metrics are in [PHASE3_BASELINE_EDGE_CENSUS.md](PHASE3_BASELINE_EDGE_CENSUS.md).

Phase 3B added immutable, ignored public-OKX BTC/ETH/SOL `1h` artifacts (8,999 clean candles per symbol, 2025-09-05 through 2026-09-15) and a protected chronological experiment design. Its sole development-selected overlay, `H2_CONFIDENCE_CEILING_70`, was rejected after validation because its reconciled base-cost PF (`2.2284`) was below the baseline PF (`2.5064`), despite positive validation expectancy. `BLIND_HOLDOUT` and the Phase 3A `LEGACY_SEEN_TEST` were not opened. This is research evidence only and does not alter the frozen Phase 3A baseline, runtime strategy behavior, PAPER, LIVE, or copy execution. See [PHASE3B_EDGE_SURGERY.md](PHASE3B_EDGE_SURGERY.md).

Phase 3C decomposed the same frozen decision-time Analyzer evidence into exhaustive causal composition families. The sole development-registered family overlay, `F1_COUNTERTREND_REVERSAL_LONG`, was rejected in validation because it produced 20 fills, below the declared 30-fill minimum. Consequently, neither `BLIND_HOLDOUT` nor `LEGACY_SEEN_TEST` was accessed. These conditional cohorts are a hypothesis-generating historical replay observation, not a profitability or LIVE-readiness claim. See [PHASE3C_EDGE_DECOMPOSITION.md](PHASE3C_EDGE_DECOMPOSITION.md) and [PHASE3C_VALIDATION.md](PHASE3C_VALIDATION.md).

The derivatives reconstruction adds a separate, immutable Binance USD-margined historical generation. It contains venue-explicit BTC/ETH/SOL 1h perpetual, mark, index, premium, spot, settled-funding, and OI/positioning observations for 2024-07-01 through 2026-06-30. Only `DERIV_DEV` was opened for alpha outcomes. Twelve primary 1h and nine single-cycle 4h variants were all rejected; the best adequate 4h candidate had `+0.011520R`, PF `1.0274`, and failed cost, temporal, drawdown, and bootstrap gates. `DERIV_VALIDATION`, `DERIV_BLIND`, old `BLIND_HOLDOUT`, and `LEGACY_SEEN_TEST` remained inaccessible. This is Binance-specific historical research evidence and cannot be assumed transferable to OKX/BingX. See [DERIVATIVES_ALPHA_RESULTS.md](DERIVATIVES_ALPHA_RESULTS.md).

## Flow / microstructure generation (2026-09-19)

The repository now contains immutable, venue-explicit Binance USD-M BTC/ETH/SOL 5-minute aggregates with genuine exchange-reported taker-buy volume, plus causally aligned OI and settled funding. Materialization `fc1879232f3149d7` has manifest SHA-256 `4c0bbd0bacc6aaaaa7a96fed6967af9e7ac524dc6bb9e15d0b08a19810a94fcf`. Taker-sell volume is the total-volume residual, and delta/CVD use aggressor flow rather than candle color.

Only `FLOW_DEV` was opened for outcomes. Twelve directional variants and the single allowed nine-candidate cross-sectional cycle all failed their preregistered Development gates. The least-negative directional result was `7434c2ca316a0606` at `-0.235183R`, PF `0.5995`; the least-negative relative-value result was `0e75190a5fc45a6e` at `-0.308721R`, PF `0.1988`. `FLOW_VALIDATION`, `FLOW_BLIND`, prior Blind samples, and `LEGACY_SEEN_TEST` remained sealed.

Historical liquidation events and reconstructable L2 were not certified, were not fabricated, and were not treated as zero. The 21-candidate registry reconciles without error in `research_artifacts/flow_alpha/reconciliation-272b7c00bfbdad04.json`. The bounded status is `NO VERIFIED FLOW EDGE`; see [FLOW_ALPHA_RESULTS.md](FLOW_ALPHA_RESULTS.md).

## Forward microstructure evidence generation (2026-09-20)

The preregistered real-time lab is operational for public Binance, OKX, and BingX BTC/ETH/SOL perpetual data. A short credential-free engineering certification connected all venues, recorded 2,090 events with zero final book gaps, and reproduced all 93 Shadow decision identities plus all 164 feature identities from the raw ledger. These certification observations are explicitly excluded from candidate evidence and were not inspected for profitability.

The persistent forward ledger begins a new unseen evidence boundary. It can accumulate raw trades, reconstructable/bounded books, liquidation events where available, OI/funding/mark/index context, causal features, frozen Shadow decisions, and delayed outcomes without Codex intervention. No candidate is eligible for review before all predeclared duration, sample, regime, cost, latency, venue, and replay gates pass. Current evidence status is `FORWARD_EVIDENCE_PENDING`; LIVE remains disabled.

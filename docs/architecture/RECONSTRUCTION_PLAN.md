# Reconstruction Plan

The long-term reconstruction allocation is approximately **70% engineering effort toward trading correctness, research, data integrity, risk, execution, and truth**, and **30% toward product, UI, and features**. Phases 1A and 1B deliberately sit inside the 70% correctness allocation. The complete pre-change analysis is in [PHASE_0_AUDIT.md](../../PHASE_0_AUDIT.md).

## Phase 1B — complete: unified decision authority

Phase 1B establishes `DecisionQualityEngine` as the single semantic boundary between analysis/observation and a trade-capable signal.

Implemented:

1. Manual, scanner, automated watch, and background observation analysis use `Analyzer`/`TradePlanIntegrity` -> `ProbabilityEngine` -> `DecisionQualityEngine`.
2. The decision contract is versioned as `decision-authority-v1` and records source, path, timestamp, outcome, data-quality state, and veto reasons.
3. LONG/SHORT alone is insufficient: only explicit APPROVED output can be promoted; NO_TRADE and malformed or stale/invalid candidates remain observations.
4. `SignalRecorder` no longer defaults missing `decision_gate_passed` to true and no longer supplies a fallback LONG side.
5. Provenance is persisted in existing JSON metadata without a schema migration.
6. `ExecutionValidator` independently checks the persisted approval marker before PAPER or LIVE/copy planning.
7. AI, market intelligence, research, and EdgeDiscovery remain advisory.

Deliberate limits:

- Strategy formulas, indicators, weights, thresholds, and historical outcomes are unchanged.
- Existing recorder lifecycle, confidence, RR, portfolio, duplicate, and premium constraints remain downstream persistence/risk guards; they do not create approval.
- Historical signals without provable Phase 1B provenance fail closed for new copy planning and are not automatically backfilled.
- PAPER and LIVE execution engines remain separate; only their upstream semantic input is aligned.
- Full market-data freshness/gap reconstruction remains Phase 2 work.

## Preserve in Phase 1A

- Existing Telegram product behavior, commands, menus, localization, and strategy semantics.
- Existing fail-closed environment, account, credential, confirmation, certification, readiness, risk, sizing, daily-loss, and kill-switch gates.
- Existing exchange protocol and BingX adapter methods; no new exchange integration layer.
- Existing immutable intent, deterministic client ID, attempt journal, unique constraints, compare-and-set transitions, audit records, and queue ownership.
- Existing live-copy, emergency-close, VST certification, worker, and startup-service boundaries.
- PAPER, SHADOW, LIVE_DRY_RUN, and disabled-mode semantics.

## Reconstruct in Phase 1A

1. Interpret the immediate adapter response instead of forcing every successful call to `ACKNOWLEDGED`.
2. Add the explicit `RECONCILING` lifecycle state and make ambiguity states safety-blocking.
3. Use direct order lookup, client ID, open orders, fills, and positions as a bounded evidence sequence.
4. Rebuild execution aggregates and fill-derived positions idempotently from exchange evidence.
5. Use an exchange order aggregate only when detailed fill history is temporarily absent, replacing it safely when complete fill IDs arrive.
6. Reconcile in-flight states at startup and periodically without submitting a new order.
7. Persist reconciliation mismatch identities deterministically so repeated runs do not duplicate the same unresolved event.
8. Add focused regression tests for immediate/partial/terminal orders, timeout ambiguity, provider failure, restart recovery, and duplicate replay.

## Deliberate limits

- No real exchange call is made by this work or its tests.
- No credentials, environment gates, limits, strategy behavior, sizing policy, or user interface are changed.
- Reconciliation does not invent an order or fill from a position alone. An unattributed exchange position remains a critical mismatch.
- Reconciliation never auto-closes positions and never auto-enables a suspended account.
- Provider-wide unavailability remains fail-closed.
- Product/UI feature work remains in the 30% allocation and is intentionally not pulled into this execution-truth phase.

## Completion evidence

Phase 1A is complete only when focused tests pass, the full baseline is rerun, all new failures are resolved, and the six known pre-existing failures remain clearly separated from regressions. Passing mocked tests is necessary evidence, not proof that production LIVE trading is safe.

Phase 1B is complete only when every production recorder route has explicit authority provenance, direct unapproved recorder/planner inputs fail closed, route-consistency tests pass, Phase 1A remains unchanged, and the full suite has no unexplained new regression. This establishes decision integrity; it is neither profitability evidence nor a production-readiness certification.

## Phase 2A — complete: market-data truth and candle integrity

Implemented:

1. A single `DataIntegrityEngine.prepare_market_frame` boundary normalizes provider candle order only when unambiguous and rejects invalid OHLCV before `UnifiedAnalysisPipeline` feature stages.
2. Runtime candle frames are UTC open-time, closed-only, unique, finite, structurally valid, timeframe-resolved, gap-checked, future-checked, fresh, and sufficiently long for the existing 220-candle pipeline requirement.
3. The boundary records `VALID`, `STALE`, `INCOMPLETE`, `GAPPED`, `CONFLICTING`, `INSUFFICIENT`, `PROVIDER_ERROR`, `UNKNOWN`, or `INVALID` data truth, plus source provenance.
4. `Market` uses provider/symbol/timeframe/limit cache keys and revalidates timestamp freshness after every cache read.
5. Invalid provider data skips strategy feature stages, becomes an explicit NO_TRADE analysis envelope, and is vetoed by the established `DecisionQualityEngine` / recorder / planner authority contract.

Deliberate limits:

- There is one public OKX REST candle source; no fallback or websocket provider was introduced.
- Direct historical/research frames retain structural checking but are labeled timing-unverified unless marked as provider runtime input; they do not grant an execution freshness claim.
- The 1.25-timeframe freshness allowance is a publication-lag structural tolerance, not a trading threshold.
- No indicators, strategy formulas, score gates for performance, order behavior, or LIVE readiness were changed.

The detailed contract and actual runtime map are in [MARKET_DATA_CONTRACT.md](MARKET_DATA_CONTRACT.md). Phase 2B should validate research/outcome quality and profitability hypotheses using the now-truthful decision-time candle provenance; it must not infer profitability from Phase 2A.

## Phase 2B — complete: research truth and replay integrity

Implemented:

1. `HistoricalReplayEngine` is a side-effect-free, closed-candle historical evaluation boundary with UTC/Phase 2A-compatible frame validation, explicit decision/entry/fill/exit clocks, deterministic costs, and immutable outcome provenance.
2. Market fills occur only at the next eligible candle open with modeled slippage; limits cannot fill before their decision and record ambiguous entry-bar behavior conservatively.
3. Stops/targets handle gaps at the available candle open and resolve same-candle stop/target ambiguity as conservative stop-first. MFE/MAE exclude pre-fill and post-exit extrema.
4. Outcome records distinguish intended, simulated, and actual fill; gross/net PnL, entry/exit fees, modeled funding status, R, ambiguity flags, mode, and provenance remain separate.
5. Chronological split, walk-forward window, dataset/configuration/run hashes, mode-segregated metric, and reproducible attribution primitives are available for later Phase 3 experiments.

Deliberate limits:

- The existing `ResearchEngine`/`EdgeDiscoveryEngine` snapshot and outcome tables are still observational/counterfactual records, not retroactively certified OHLC replay.
- No historical funding series, bid/ask history, leverage/liquidation model, or actual LIVE-fill dataset is available locally; missing funding is explicitly `NOT_MODELED`.
- No production DB state, strategy setting, decision threshold, sizing policy, symbol selection, or execution path was changed.

The contract is documented in [RESEARCH_CLOCK_CONTRACT.md](RESEARCH_CLOCK_CONTRACT.md), [RESEARCH_EXECUTION_MODEL.md](RESEARCH_EXECUTION_MODEL.md), and [PERFORMANCE_METRIC_CONTRACT.md](../research/PERFORMANCE_METRIC_CONTRACT.md). Current local evidence is classified in [AVAILABLE_EVIDENCE.md](../research/AVAILABLE_EVIDENCE.md): it is insufficient for a profitability claim.

## Phase 3A — complete: baseline edge census

The frozen current stack is replayed only through the side-effect-free Phase 3A adapter. It materializes public OKX candle datasets before replay, captures every decision, invokes the existing `Analyzer`/`TradePlanIntegrity`/`DecisionQualityEngine` stack, and routes approved plans through the Phase 2B replay model. Runtime persistence and economic paths remain excluded. The baseline and Phase 3B comparison rules are documented in [PHASE3_BASELINE_CONFIG.md](../research/PHASE3_BASELINE_CONFIG.md), [PHASE3_BASELINE_EDGE_CENSUS.md](../research/PHASE3_BASELINE_EDGE_CENSUS.md), and [EDGE_COMPARISON_CONTRACT.md](../research/EDGE_COMPARISON_CONTRACT.md).

The frozen run contains 2,337 eligible decisions, 321 approvals, 2,016 `NO_TRADE` decisions, 247 modeled fills, and 74 expired entries. Its aggregate expectancy is positive under the stated model, but the final-test slice is negative and the 41-day evidence is classified as unstable rather than live-profitability or production-readiness proof. Phase 3B may test the documented hypotheses; it must retain the frozen datasets and comparison gates and must not overwrite the Phase 3A artifact.

## Phase 3B — complete: controlled edge surgery, no qualified candidate

Phase 3B preserved the Phase 3A parent (`84533946dfbbbf65`) and evaluated a materially longer immutable public-OKX BTC/ETH/SOL `1h` dataset under a protected development/validation/blind/legacy chronology. Analyzer contribution attribution was read-only, existing decisions stayed authoritative, and research-only exit-protection semantics were constrained to next-candle activation.

The sole development-selected admission overlay, `H2_CONFIDENCE_CEILING_70`, was positive in validation but degraded reconciled base-cost net-PnL PF below the baseline. It therefore failed the pre-registered gate and was not promoted or frozen as a blind finalist. `BLIND_HOLDOUT` and `LEGACY_SEEN_TEST` were deliberately not accessed. No production defaults, Analyzer score, DecisionQuality rule, PAPER/LIVE/copy execution path, or Phase 3A artifact changed. The full result and limitations are in [PHASE3B_EDGE_SURGERY.md](../research/PHASE3B_EDGE_SURGERY.md) and [PHASE3B_BLIND_VALIDATION.md](../research/PHASE3B_BLIND_VALIDATION.md).

## Phase 3C — complete: decomposition, no finalist

Phase 3C kept the Phase 3A/3B frozen data, cost, replay, and protected split boundaries intact. It classified current Analyzer score-component evidence into an exhaustive, outcome-free family map. The only development-supported family, a LONG countertrend reversal requiring existing trend-conflict plus CHOCH-confirmation evidence, had a positive but insufficient 20-fill validation sample. It therefore failed the declared minimum-sample gate.

No Phase 3C finalist was frozen. `BLIND_HOLDOUT` and `LEGACY_SEEN_TEST` remain unopened. Production Analyzer/DecisionQuality defaults, strategy authority, PAPER/LIVE/copy paths, and the Phase 3A/3B reports remain unchanged. See [PHASE3C_EDGE_DECOMPOSITION.md](../research/PHASE3C_EDGE_DECOMPOSITION.md), [PHASE3C_STRATEGY_HYPOTHESES.md](../research/PHASE3C_STRATEGY_HYPOTHESES.md), and [PHASE3C_VALIDATION.md](../research/PHASE3C_VALIDATION.md).
## Derivatives alpha bounded cycle

The research-only derivatives architecture is complete through its protocol stop condition. It adds immutable public-data materialization, per-input timestamp/availability/age fields, backward-only alignment, independent freshness thresholds, structural integrity states, causal positioning features, fixed candidate identities, actual funding costs, cluster-aware uncertainty, and fail-closed predicates. No production module imports a derivatives research artifact.

Primary 1h and the single permitted 4h follow-up produced no Development qualifier. Validation and Blind were not opened, so no challenger integration, Shadow/PAPER promotion, portfolio overlay, or cross-venue production transfer was permitted. The exact next research step is external to this bounded program: acquire a separately certifiable new information source (for example trustworthy liquidation/flow history) and preregister a new program, rather than tune these rejected candidates.

## Flow / microstructure research — complete through bounded stop

Implemented research-only genuine taker-flow materialization, causal delta/CVD and OI/funding alignment, completed 5m/15m aggregation, immutable candidate identities, three cost scenarios, chronological folds, clustered uncertainty, DecisionQuality admission, and fail-closed Shadow data state. The official Binance venue is explicit in every row and artifact.

Exactly 12 directional and 9 cross-sectional Development candidates were rejected. Validation, Blind, and Legacy samples remained unopened; no research module is connected to production runtime execution. Because both permitted cycles failed, further historical indicator mining is prohibited by the program. The next architecture increment, under a new preregistration, is append-only forward public event collection for certified liquidation/raw-trade/book evidence with disconnect, sequence, staleness, duplicate, out-of-order, and resync controls.

## Forward microstructure alpha lab — operational

The next architecture increment is complete. Credential-free Binance, OKX, and BingX connectors feed an append-only raw ledger with exchange/receive clocks, duplicate identity, connection provenance, recovery checkpoints, strict Binance/OKX book continuity, and honest BingX snapshot-only depth. Sequence failure invalidates the book and fails candidate evaluation closed.

The causal engine, cross-venue state, five-family/two-variant frozen registry, conservative three-latency Shadow execution, delayed label/PnL sink, status dashboard, and receive-order deterministic replay are isolated from all production execution paths. The full suite is 599 passed with only the six established pre-existing failures. Forward evidence must now accumulate for at least the frozen 30-day/sample gate; no historical retuning or production promotion is allowed.

# Test Baseline

## Pre-change baseline

Command environment: bundled Python, repository plus `.codex-test-deps` on `PYTHONPATH`, and a syntactically valid dummy bot token. No exchange credentials or real network calls were used.

Result: **479 passed, 6 failed in 72.00 seconds**.

Known pre-existing failures:

1. `test_market_intelligence_v102_separates_scores_and_fusion` expects `entry-readiness-v3`; implementation returns v4.
2. `test_alert_engine_v3_records_usage_delivery_and_unchanged_state` expects alert-engine-v3; implementation returns v4.
3. `test_copy_analytics_v2_empty_state_and_public_error_sanitizer` expects paper-copy-analytics-v2; implementation returns v3.
4. `test_quality_readiness_scanner_and_fusion_v3_semantics` expects `entry-readiness-v3`; implementation returns v4.
5. `test_readiness_reports_every_failure_and_can_pass` omits current balance/risk inputs and receives `BALANCE_READ_FAILED` and `RISK_PROFILE_INCOMPLETE`.
6. `test_pump_and_dump_reversal_distinguish_continuation_from_exhaustion` receives `PUMP_CONTINUATION`, outside its older expected reversal-state set.

## Phase 1A focused suite

`tests/test_phase1a_live_execution_truth.py` covers:

- recognized open response;
- immediate full fill with actual quantity, weighted price, and fees;
- partial then full fill;
- response-status rejection and cancellation;
- explicit exchange rejection;
- timeout accepted, timeout filled, and timeout lost;
- transient lookup-provider failure;
- closed/filled order absent from open orders;
- fills-only recovery and position reconstruction;
- restart recovery from `SUBMITTING`, `SUBMITTED`, and `UNKNOWN`;
- duplicate intent conflict, duplicate fill replay, and duplicate reconciliation events.

Focused Phase 1A file result: **17 passed in 4.24 seconds**.

Combined Phase 1A plus durable LIVE execution result after final edits: **28 passed, 1 deselected in 5.48 seconds**. The deselected case is the known stale readiness fixture listed above. A broader changed-component run (production hardening, BingX adapter, and live copy included) passed **50 tests with the same one stale fixture deselected**.

## Post-change full suite

Result: **496 passed, 6 failed in 66.92 seconds**.

The six failures are exactly the six pre-change failures listed above. There are **no new test regressions** in this run. The net increase of 17 passing cases is the new Phase 1A focused test module.

## Phase 1B verification

Environment remained the bundled Python runtime with repository plus `.codex-test-deps` on `PYTHONPATH` and a syntactically valid dummy bot token. No exchange credentials, network calls, or real orders were used.

- New Phase 1B authority tests: **8 passed in 1.06 seconds**.
- Final authority/planner/engine smoke group: **18 passed in 1.56 seconds**.
- Copy planning, journal, PAPER lifecycle, portfolio, and provenance group: **112 passed** across the two non-overlapping copy runs (95 + 17).
- Decision/analyzer/watch group: **47 passed, 1 failed** before the final added negative case; the failure is known baseline item 4.
- Phase 1A/LIVE regression group: **50 passed, 1 failed**; the failure is known baseline item 5. The focused Phase 1A module itself remains **17 passed**.

The first full run after enforcing copy provenance produced 15 additional failures from tests that fabricated legacy ACTIVE signals without decision metadata. Classification: **OUTDATED_CONTRACT**. Only those directly affected execution fixtures were updated to represent an explicit `decision-authority-v1` approval; negative tests preserve proof that provenance-free inputs fail closed.

Final full-suite result on the Phase 1A + Phase 1B tree: **504 passed, 6 failed in 68.00 seconds**. All six failures are the exact **PRE_EXISTING** cases listed above. Compared with the Phase 1A baseline of 496/6, Phase 1B adds eight passing tests, preserves the six known failures, and introduces **0 NEW_REGRESSION**, **0 ENVIRONMENT_DEPENDENT**, and **0 UNKNOWN** failures.

## Phase 2A verification

Environment remained the bundled Python runtime with repository plus `.codex-test-deps` on `PYTHONPATH`. All market tests use deterministic local frames, injected UTC reference clocks, and local fake providers/cache entries; no public network, credentials, private API, order submission, or database migration was used.

- New `tests/test_phase2a_market_data_contract.py`: **16 passed in 0.69 seconds**. It covers regular 1m/4h closed data, reverse-order normalization, forming-candle exclusion, timeframe-based freshness and future candles, gaps, exact/conflicting duplicates, malformed/negative/non-finite/impossible OHLCV, insufficient data, unknown timeframe, provider timeout/malformed response, stale cache, and NO_TRADE route-source parity.
- Phase 2A + Phase 1A/1B/recorder-planner regression group: **69 passed in 5.71 seconds**. This includes the Phase 2A module, Phase 1A LIVE truth, Phase 1B authority, copy profile/planning/validation/engine tests, and legacy decision-quality compatibility checks.
- Final full suite: **520 passed, 6 failed in 74.00 seconds**.

The six failures are the same **PRE_EXISTING** failures already listed above: three older v10.2 version assertions, one older v10.3 entry-readiness version assertion, the readiness fixture missing current balance/risk inputs, and the pump/reversal expectation outside the current state set. Relative to the 504/6 Phase 1A+1B baseline, Phase 2A adds **16 passing tests**, preserves all six baseline failures, and introduces **0 NEW_REGRESSION**, **0 ENVIRONMENT_DEPENDENT**, and **0 UNKNOWN** failures.

## Phase 2B verification

Environment remained the bundled Python runtime with repository plus `.codex-test-deps` on `PYTHONPATH` and a syntactically valid dummy bot token for the full run. Replay tests use deterministic in-memory UTC candles and no provider, credential, database-write, order, PAPER, or LIVE path.

- New `tests/test_phase2b_research_truth.py`: **13 passed in 1.32 seconds**. Coverage includes closed-candle visibility, future-row isolation, post-decision MARKET/LIMIT eligibility, SL/TP/gap and ambiguous-bar policy, fee/slippage/funding semantics, MFE/MAE boundaries, Phase 2A gap rejection, chronological split/walk-forward, dataset/config provenance, mode anti-mixing/relabeling, deterministic attribution, and the static no-runtime-authority import boundary.
- Phase 2B + Phase 2A + Phase 1B + Phase 1A + legacy backtest/research combined suite: **76 passed in 15.43 seconds**.
- Existing research/backtest/metrics/outcome group: **74 passed in 19.32 seconds**.
- Final full suite: **533 passed, 6 failed in 68.26 seconds**.

The six failures are the exact same **PRE_EXISTING** node IDs listed above. Relative to the Phase 2A baseline of 520/6, Phase 2B adds **13 passing tests**, preserves all six baseline failures, and introduces **0 NEW_REGRESSION**, **0 OUTDATED_CONTRACT**, **0 ENVIRONMENT_DEPENDENT**, and **0 UNKNOWN** new failures. The existing baseline version/assertion issues remain classified only as PRE_EXISTING, not as Phase 2B regressions.

## Phase 3A verification

The environment remained the bundled Python runtime with the repository plus `.codex-test-deps` on `PYTHONPATH` and a syntactically valid dummy bot token. The final verification was local-only: it did not refetch public data, rerun the frozen census, use credentials, write a database, submit an order, or invoke PAPER/LIVE/copy paths.

- New `tests/test_phase3a_baseline_census.py`: **3 passing tests** within the final full suite. The compact cases cover deterministic config and dataset identities, relevant config/candle mutation, invalid Phase 2A dataset rejection, historical snapshot isolation, research-side-effect boundaries, confidence bucketing, loss classification, and the insufficient-sample comparison guard.
- The earlier combined Phase 3A + Phase 2B + Phase 2A + Phase 1B + Phase 1A focused regression set passed **57 tests**.
- Final full suite: **536 passed, 6 failed in 53.34 seconds**.

The six failures are exactly the six **PRE_EXISTING** node IDs listed at the start of this document:

1. `tests/test_v102_multilingual_autonomous_platform.py::test_market_intelligence_v102_separates_scores_and_fusion`
2. `tests/test_v102_multilingual_autonomous_platform.py::test_alert_engine_v3_records_usage_delivery_and_unchanged_state`
3. `tests/test_v102_multilingual_autonomous_platform.py::test_copy_analytics_v2_empty_state_and_public_error_sanitizer`
4. `tests/test_v103_operational_intelligence.py::test_quality_readiness_scanner_and_fusion_v3_semantics`
5. `tests/test_v9910_durable_live_execution.py::test_readiness_reports_every_failure_and_can_pass`
6. `tests/test_v9918_market_intelligence.py::test_pump_and_dump_reversal_distinguish_continuation_from_exhaustion`

Relative to the Phase 2B baseline of 533/6, Phase 3A adds **3 passing tests**, preserves all six baseline failures, and introduces **0 NEW_REGRESSION**, **0 OUTDATED_CONTRACT**, **0 ENVIRONMENT_DEPENDENT**, and **0 UNKNOWN** new failures.

## Phase 3B verification

The environment remained the bundled Python runtime with the repository plus `.codex-test-deps` on `PYTHONPATH` and a syntactically valid local dummy bot token. Verification was local-only: it reused the already materialized immutable Phase 3B artifacts, did not refetch market data, did not rerun the frozen Phase 3A census, and did not exercise PAPER, LIVE, copy execution, or credentials.

- `tests/test_phase2b_research_truth.py` plus `tests/test_phase3b_edge_surgery.py`: **16 passed in 1.56 seconds**. The Phase 3B cases cover split/blind/legacy access enforcement, deterministic candidate identities, immutable admission overlays, development-only monotonic calibration, and causally next-candle stop protection. Existing Phase 2B cases continue to cover costs, Phase 2A frame integrity, and replay chronology.
- Static compilation passed for the Phase 3B research adapter, replay engine, and OKX historical helper.
- Final full suite: **539 passed, 6 failed in 74.93 seconds**.

The six failures are exactly the **PRE_EXISTING** node IDs carried from the 536/6 Phase 3A baseline:

1. `tests/test_v102_multilingual_autonomous_platform.py::test_market_intelligence_v102_separates_scores_and_fusion`
2. `tests/test_v102_multilingual_autonomous_platform.py::test_alert_engine_v3_records_usage_delivery_and_unchanged_state`
3. `tests/test_v102_multilingual_autonomous_platform.py::test_copy_analytics_v2_empty_state_and_public_error_sanitizer`
4. `tests/test_v103_operational_intelligence.py::test_quality_readiness_scanner_and_fusion_v3_semantics`
5. `tests/test_v9910_durable_live_execution.py::test_readiness_reports_every_failure_and_can_pass`
6. `tests/test_v9918_market_intelligence.py::test_pump_and_dump_reversal_distinguish_continuation_from_exhaustion`

Relative to Phase 3A, Phase 3B adds **3 passing tests**, retains those same six failures, and introduces **0 NEW_REGRESSION**. The validation candidate was rejected by the research promotion contract; no production setting changed.

## Phase 3C verification

Phase 3C reused the existing local immutable artifacts and did not refetch candles, rerun the frozen census, access `BLIND_HOLDOUT`/`LEGACY_SEEN_TEST`, or invoke PAPER/LIVE/copy execution.

- Focused Phase 1A/1B/2A/2B/3A/3B/3C regression set: **62 passed in 6.65 seconds**.
- New `tests/test_phase3c_edge_decomposition.py` verifies outcome-free exhaustive family classification, explicit UNKNOWN retention, deterministic candidate identity, and admission retention only after existing `DecisionQualityEngine` approval.
- Final full suite: **541 passed, 6 failed in 67.67 seconds**.

The six failures are unchanged **PRE_EXISTING** node IDs from the 539/6 baseline:

1. `tests/test_v102_multilingual_autonomous_platform.py::test_market_intelligence_v102_separates_scores_and_fusion`
2. `tests/test_v102_multilingual_autonomous_platform.py::test_alert_engine_v3_records_usage_delivery_and_unchanged_state`
3. `tests/test_v102_multilingual_autonomous_platform.py::test_copy_analytics_v2_empty_state_and_public_error_sanitizer`
4. `tests/test_v103_operational_intelligence.py::test_quality_readiness_scanner_and_fusion_v3_semantics`
5. `tests/test_v9910_durable_live_execution.py::test_readiness_reports_every_failure_and_can_pass`
6. `tests/test_v9918_market_intelligence.py::test_pump_and_dump_reversal_distinguish_continuation_from_exhaustion`

Relative to the Phase 3B baseline, Phase 3C adds **2 passing tests** and introduces **0 NEW_REGRESSION**. F1 was rejected for its pre-registered insufficient validation sample; no finalist, blind run, or production change exists.
## Derivatives alpha additions

`tests/test_derivatives_alpha.py` covers backward-only availability alignment, future rejection, structural integrity states, causal feature invariance, price/OI quadrant identity, immutable candidate identities, protected split chronology, directional funding cost, absence of execution authority, fail-closed predicates, long/short independence, and the bounded cycle-2 roster. Artifact reconciliation additionally verifies all dataset hashes, exact row counts, candidate rosters, and protected-access flags.

- Focused Phase 1A/1B/2A/2B/3A/3B/3C/Stage-B/derivatives regression set: **88 passed in 8.24 seconds**.
- Final full suite: **567 passed, 6 failed in 59.03 seconds**.
- The six failures are exactly the established `PRE_EXISTING` node IDs documented above; the derivatives cycle adds **13 passing tests** to the 554/6 starting baseline and introduces **0 NEW REGRESSIONS**.

## Flow / microstructure alpha additions

`tests/test_flow_microstructure.py` covers genuine exchange-taker delta/CVD semantics, invalid taker volume, completed causal aggregation, future-mutation isolation, derivatives-status propagation, bounded directional and relative-value candidate identities, final artifact reconciliation and protected-split seals, three-scenario cost monotonicity, DecisionQuality integration, and fail-closed Shadow behavior.

- Focused Phase 1A/1B/2A/2B/3A/3B/3C/F1/Stage-B/derivatives/flow regression set: **98 passed in 8.30 seconds**.
- Flow-specific suite: **10 passed in 0.43 seconds**.
- Final full suite: **577 passed, 6 failed in 69.06 seconds**.
- The six failures are exactly the established `PRE_EXISTING` node IDs documented above. Relative to the 567/6 derivatives baseline, flow research adds **10 passing tests** and introduces **0 NEW REGRESSIONS**.
- Neither `FLOW_VALIDATION` nor `FLOW_BLIND` was accessed; all 21 candidates were rejected in Development and production defaults remained unchanged.

## Forward microstructure alpha lab verification

The forward lab used only deterministic local fixtures for its test suites. A separate short public-feed run was connectivity/integrity certification, not a test dependency and not candidate evidence.

- New `tests/test_forward_microstructure.py`: **22 passed in 0.68 seconds**. Coverage includes immutable compressed raw storage, duplicates, exchange/receive clocks, Binance/OKX/BingX parsing, Binance snapshot bridging and gaps, current OKX zero-checksum semantics plus legacy checksum validation, BingX snapshot-only limits and batched trades, causal flow/CVD, cross-venue freshness, conservative Shadow entry/exit fills, label isolation, registry identity, restart checkpoints and explicit restart-gap labels, universe filtering, no execution authority, duplicate admission, and exact receive-order replay.
- Accumulated Phase 1A/1B/2A/2B/3A/3B/3C/F1/Stage-B/derivatives/flow/forward focused suite: **120 passed in 9.02 seconds**.
- Final full suite: **599 passed, 6 failed in 59.96 seconds**.

The six failures are exactly the same **PRE_EXISTING** node IDs listed above. Relative to the 577/6 historical-flow baseline, the forward lab adds **22 passing tests**, preserves the six known failures, and introduces **0 NEW_REGRESSION**, **0 OUTDATED_CONTRACT**, **0 ENVIRONMENT_DEPENDENT**, and **0 UNKNOWN** new failures.

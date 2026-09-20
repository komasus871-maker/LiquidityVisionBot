# Phase 3B Pre-registered Hypotheses and Evaluation Design

Status: split and hypothesis design registered before validation/blind access; development-only candidate roster frozen on 2026-09-15 before validation access.

## Immutable parent

The parent remains Phase 3A frozen baseline `84533946dfbbbf65`, run `a0828246973815e6`. It is never overwritten or relabelled. The existing 2026-08-03 through 2026-09-13 materialized candle period is `LEGACY_SEEN_TEST`; it is robustness-only evidence and is excluded from development, validation, candidate selection, and blind selection.

## Expanded data and temporal design

The fixed universe is BTC, ETH, and SOL USDT swaps at `1h`. The Phase 3B fetch targets the longest deterministic contiguous public-OKX history available up to 9,000 closed candles per symbol. The legacy interval is retained in the materialized artifact but separated by timestamp.

The unseen pre-legacy segment ends strictly before `2026-08-03T01:00:00Z`. Its chronological split is declared before strategy evaluation: first 55% `DEVELOPMENT`, next 20% `VALIDATION`, and final 25% `BLIND_HOLDOUT`. The blind partition is unavailable to development/validation evaluation APIs. All intervals preserve a 220-bar feature warmup; warmup rows never become decisions. `LEGACY_SEEN_TEST` may only be evaluated after the final candidate has been frozen.

## Candidate rules

Candidates are research-only overlays. They never modify `Analyzer`, `DecisionQualityEngine`, SignalRecorder, PAPER, LIVE, copy execution, or runtime defaults. Every candidate has a deterministic ID/config hash, its parent hash, an explicit tested-variant count, source/dataset hashes, and a recorded outcome. No Cartesian or threshold grid search is permitted.

### H1 — SHORT admission quality

Investigate whether poor SHORT outcomes persist on development data and whether causal Analyzer evidence distinguishes them. At most two bounded variants may be selected after development inspection: one stronger-directional-evidence gate and one countertrend/structure compatibility gate, each using only fields already emitted at decision time. SHORT is not disabled wholesale.

### H2 — confidence calibration

Fit a simple monotonic calibration mapping on DEVELOPMENT decisions only. It affects research probability/display diagnostics only unless a separately registered admission candidate is created. The calibration artifact, source window, seed, and version must be preserved. It must not be fit or refit on validation, blind, or legacy data.

### H3 — exit/profit protection

Compare current static exits with at most two executable, chronological variants selected from development excursion distributions: one breakeven/protection activation and one bounded profit-lock/trailing rule. Stop changes can use only already-observed prices and must retain Phase 2B conservative same-bar semantics.

### H4 — low-quality/chop selectivity

Use existing decision-time Analyzer evidence only—such as directional edge, execution readiness, trend-strength, volume, structure, or verified regime—to test at most two selective admission overlays. A regime gate is blocked unless the all-decision regime diagnostic demonstrates a non-degenerate causal label.

### H5 — symbol and cost robustness

Evaluate every baseline/candidate across BTC, ETH, and SOL. Apply the fixed, outcome-independent scenarios `BASE_COST` (fee `0.0005`, market slippage `0.0003`), `HIGHER_COST` (fee `0.00075`, market slippage `0.0005`), and `STRESS_COST` (fee `0.0010`, market slippage `0.0010`); funding remains `NOT_MODELED` in every scenario. Symbol-specific filters may be candidates only after persistent multi-fold OOS weakness, never from Phase 3A alone.

## Development-only roster freeze

The following diagnostic was read from `development-baseline-5676fb4a9b2c5290.json` only. Its 741 filled decisions had expectancy `-0.170647793036R` and PF `0.890022932368`; this is not validation evidence. The causal all-decision regime labels were varied, but 961 of 966 approved decisions were `TRANSITION`, so a regime-based overlay is not discriminative at the approval boundary and is blocked. Execution-readiness did not yield a robust independent gate. SHORT directional-edge thresholds remained negative. Of the 440 losing fills, only 14 had reached `1R` MFE; even the impossible upper bound of recovering a full `1R` on every one leaves the development expectancy negative, so H3 has no exit candidate for validation.

One and only one admission candidate is frozen for validation:

| Candidate ID | Hypothesis | Parameters | Development-only rationale |
| --- | --- | --- | --- |
| `H2_CONFIDENCE_CEILING_70` | H2 | `max_confidence: 70` | The `<=70` subset had 255 fills, `+0.018609R` expectancy and PF `1.0381`; this bounded overlay prevents the anomalously poorer high-confidence labels from passing while calibration is investigated. |

The candidate is an admission-only research overlay. It uses the already emitted decision-time confidence field, does not recalculate it, and leaves Analyzer, DecisionQualityEngine, replay entry/exit semantics, costs, production configuration, and all split boundaries unchanged. No other development threshold is eligible for validation. The H2 PAV calibration is diagnostic-only and is fit once from DEVELOPMENT outcomes; it does not alter candidate admissions.

## Promotion gate, declared before blind access

A candidate reaches `FINALIST` only after frozen validation and walk-forward results show: at least 30 OOS fills, positive aggregate OOS expectancy-R, no catastrophic fold, non-degrading PF, acceptable drawdown relative to the baseline, explicit cost robustness, no leakage, and no Phase 1A/1B/2A/2B regression. At most one primary candidate and one conservative alternate may be frozen. A frozen finalist alone may unlock blind evaluation, with no later modification permitted in Phase 3B.

Higher WR, lower drawdown alone, one positive symbol, or a smaller trade count is not sufficient. A failed candidate remains in the experiment register.

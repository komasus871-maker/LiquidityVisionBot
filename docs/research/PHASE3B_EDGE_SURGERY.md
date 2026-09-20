# Phase 3B Controlled Edge Surgery

## Scope and immutability

Phase 3B is an offline, research-only extension of the Phase 3A frozen parent configuration `84533946dfbbbf65` (run `a0828246973815e6`). It does not rewrite Phase 3A artifacts, Analyzer logic, DecisionQuality logic, replay defaults, data-provider runtime behavior, SignalRecorder, PAPER, LIVE, copy execution, or messaging.

The immutable expanded public-OKX `1h` artifacts cover BTC, ETH, and SOL swaps. Each has 8,999 Phase 2A-integrity-valid candles from `2025-09-05T12:00:00Z` through `2026-09-15T10:00:00Z`:

| Symbol | Dataset ID | Content hash prefix |
| --- | --- | --- |
| BTC | `03265d42212ccd3c` | `03265d42212ccd3c` |
| ETH | `b3e5c9de0b38c779` | `b3e5c9de0b38c779` |
| SOL | `f51f8064a8b11fc1` | `f51f8064a8b11fc1` |

The artifacts are ignored local research evidence. They are read-only inputs after materialization; they are not live market data and make no profitability or LIVE-performance claim.

## Chronological protocol

The pre-registered split design is `55% DEVELOPMENT`, `20% VALIDATION`, and `25% BLIND_HOLDOUT` across the pre-legacy history, with a 220-bar feature warmup and 144-bar settlement buffer separating decision windows. `LEGACY_SEEN_TEST` starts at `2026-08-03T01:00:00Z`, the beginning of the already seen Phase 3A material. The runner makes the full settlement tail available only to settle decisions in its own role; it never admits tail decisions into that role.

The access guard refuses blind metrics until one candidate is frozen after validation, and refuses legacy metrics until that blind run has completed. Every decision uses the canonical expanding closed-candle snapshot, current Analyzer plus DecisionQuality authority, historical-memory neutralization, modeled costs, and the existing conservative OHLC stop-first semantics.

## Candidate construction

`services/phase3b_edge_surgery.py` is an offline-only adapter. It records Analyzer evidence at decision time—directional edge, score components, drivers/blockers, execution readiness, structure, volume and regime—without synthesizing a strategy label. `CandidateSpec` IDs are deterministic hashes of the parent configuration, hypothesis, and explicit parameters.

Admission-only candidates are derived from the baseline's same decision-time evidence. A retained decision keeps precisely its recorded entry, exit, cost model, and modeled outcome; a candidate that changes exits must use a new chronological replay. The one-candidate validation roster and the development-only reasons that disqualified the other hypotheses are frozen in [PHASE3B_HYPOTHESES.md](PHASE3B_HYPOTHESES.md).

The optional protection primitive is research-only. It can raise a stop only after the next candle following the observed activation; it cannot credit a bar's high and low to a same-bar stop change. This preserves the Phase 2B conservative intrabar contract.

## Registered results

| Variant | Hypothesis | Development result | Validation result | Outcome |
| --- | --- | --- | --- | --- |
| `BASELINE` | Parent | 741 fills, `-0.170648R`, PF `0.8900` on net-R | 280 fills, `+0.208171R`; reconciled net-PnL PF `2.5064` | Reference only |
| `H2_CONFIDENCE_CEILING_70` (`ab5f8d32fd4b51a7`) | H2 | 255 fills, `+0.018609R`, PF `1.0381`; BTC `-0.001579R`, ETH `+0.179421R`, SOL `-0.095441R` | 102 fills, `+0.221191R`, net-R PF `1.5339`, net-PnL PF `2.2284`, lower DD (`4,821.92` vs `9,602.79`) | **REJECTED**: base-cost net-PnL PF degrades versus baseline, so it fails the pre-registered promotion gate. |

H1 had no registered validation candidate: the development short directional-edge diagnostic remained negative even at its least restrictive useful gate (`>=40`: 310 fills, `-0.116458R`). H3 had no registered validation candidate: only 14 of 440 development losses reached `1R` MFE, so even the impossible full recovery of all 14 could not make the development expectancy positive. H4 regime gating was blocked because approvals were concentrated in `TRANSITION` despite varied all-decision labels; execution-readiness was also too discrete to produce a robust independent gate. H5 introduced no symbol disablement.

The H2 diagnostic PAV calibration fitted on development pooled the observed buckets to a flat `0.406207827260` win probability. It is evidence that the displayed confidence is not a usable monotone empirical probability in this sample; it was not applied to Analyzer or the user interface.

Validation used three fixed chronological folds. Baseline expectancy-R was `+0.356360`, `+0.060563`, and `+0.270953`; H2 was `+0.412704`, `+0.257205`, and `+0.062265`. The candidate was positive in each fold but did not improve every fold and failed the aggregate PF gate. The fixed 24-bar, seed-3103 bootstrap 95% interval was `[-0.052665, +0.434905]R` for baseline and `[+0.041527, +0.378304]R` for H2.

Cost sensitivity retained positive expectancy-R but is not a promotion substitute: baseline / H2 were `+0.208171 / +0.221191R` at base cost, `+0.153700 / +0.175083R` at higher cost, and `+0.097684 / +0.128427R` under stress. The respective stress PFs were `2.0476` and `1.8718` on reconciled net PnL. Funding remains `NOT_MODELED`.

## Evaluation and promotion

Candidate comparisons report fill count, win/loss/breakeven count, expectancy, expectancy-R, PF, net PnL, fees, drawdown, consecutive losses, MFE/MAE, dates, source/dataset/config hashes, and decision attribution. Development uncertainty uses a fixed-seed 24-bar temporal block bootstrap. Cost robustness uses the fixed base, higher-cost, and stress scenarios; it does not tune cost assumptions.

No candidate becomes production configuration from this work. A candidate can become a `FINALIST` only under the pre-declared OOS gates in the hypotheses record, then has exactly one unmodified blind evaluation. The blind decision and results are recorded in [PHASE3B_BLIND_VALIDATION.md](PHASE3B_BLIND_VALIDATION.md).

# Flow / Microstructure Alpha Results

Status: bounded Cycle A and Cycle B complete. No Development qualifier; Validation and Blind stayed sealed.

## Final deliverable

1. **Historical flow source:** official Binance USD-M public 5-minute kline archives, whose exchange fields include total volume and true taker-buy base/quote volume. OI metrics and settled funding use the same venue and are never presented as transferable OKX/BingX behavior.
2. **Available history:** the immutable research generation spans the frozen 2023-01-01 through 2026-08-31 chronology for BTCUSDT, ETHUSDT, and SOLUSDT. Only `FLOW_DEV` through 2025-09-30 was opened for outcomes.
3. **Dataset hashes:** Development hashes are BTC `f90ba211...de8116`, ETH `d0e404d4...77479`, SOL `15c027ea...1531`; all full hashes are in the materialization and registry. Manifest SHA-256 is `4c0bbd0bacc6aaaaa7a96fed6967af9e7ac524dc6bb9e15d0b08a19810a94fcf`.
4. **Integrity status:** all three split files are chronologically `VALID`. The source contains 129 BTC, 129 ETH, and 142 SOL OI-gap rows, explicitly marked `GAPPED`; funding gaps are zero. Reconciliation `272b7c00bfbdad04` is valid with no errors.
5. **CVD implementation:** `taker_sell = total - taker_buy`, `delta = taker_buy - taker_sell`, and `CVD = cumulative delta` in timestamp order. It is not candle-color volume.
6. **Flow features:** normalized delta, multi-horizon delta, CVD, rolling delta percentile/z-score, price/CVD divergence, absorption ratio, OI change/percentile, funding percentile, 1h trend context, and cross-sectional price/flow/OI dispersion.
7. **Liquidation availability:** no certifiable official historical USD-M liquidation archive was found. Missing liquidation evidence was not treated as zero and no liquidation alpha was backtested.
8. **Order-book availability:** the public historical depth sample is percentage-bucket depth rather than raw reconstructable L2; bookTicker history is large and begins materially later. Neither was used as primary historical alpha.
9. **Alpha families:** four directional families (`FLOW_CONTINUATION`, `CVD_DIVERGENCE_REVERSAL`, `ABSORPTION_REVERSAL`, `OI_FLOW_SQUEEZE`) and three cross-sectional families (`RELATIVE_FLOW_CONTINUATION`, `PRICE_FLOW_MISMATCH_REVERSION`, `OI_FLOW_DISPERSION`).
10. **Variants tested:** 12 directional plus 9 cross-sectional, exactly 3 per family and 21 total. No hidden or replacement variants exist.
11. **Conditional-information findings:** the best adequate directional conditional mean was SOL F4 LONG at 12×15m, `+0.09018%` over 5,390 observations with 50.33% positive probability. The worst was ETH F4 SHORT at 3×15m, `-0.15320%` over 269 observations with 37.17% positive probability. Small raw conditional effects did not survive executable costs/stops.
12. **Feature ablations:** adding flow to structure was mixed (BTC and SOL worsened, ETH improved slightly). Adding OI materially increased mean raw F4 forward returns; funding was mixed. Those descriptive increments did not yield positive trade expectancy.
13. **Walk-forward results:** every directional best-candidate fold was negative (`-0.2844R`, `-0.1927R`, `-0.2543R`, `-0.2171R`). Every cross-sectional best-candidate fold was also negative (`-0.3937R`, `-0.2545R`, `-0.2910R`, `-0.3139R`).
14. **Validation results:** not run; zero candidates passed the frozen Development promotion contract.
15. **Blind status/results:** `FLOW_BLIND` was never accessed. Prior Blind and Legacy samples also remained untouched.
16. **Best candidate:** least-negative directional candidate `F4_OI_FLOW_SQUEEZE_15M:STRICT`, identity `7434c2ca316a0606`. The least-negative cross-sectional candidate was `RV3_OI_FLOW_DISPERSION:BASE`, identity `0e75190a5fc45a6e`.
17. **Expectancy-R:** best directional `-0.235183R`; best relative-value `-0.308721R` under BASE costs.
18. **Profit factor:** best directional `0.5995`; best relative-value `0.1988`.
19. **Win rate:** best directional `39.92%`; best relative-value `20.35%`.
20. **Drawdown:** best directional `2,623.15R`; best relative-value `763.16R`. Both are far outside promotion limits.
21. **Costs:** BASE/HIGH/STRESS include two-sided fees, entry/exit friction, and actual aligned funding settlements. Directional expectancy fell from `-0.2352R` to `-0.4357R` and `-0.6824R`; relative-value fell from `-0.3087R` to `-0.5477R` and `-0.8418R`.
22. **Effective sample / cluster N:** best directional 11,037 fills / 5,792 hourly clusters; best relative-value 2,472 fills / 1,644 four-hour clusters. Failure is not attributable to an undersized sample.
23. **LONG/SHORT breakdown:** directional best was LONG 10,572 fills at `-0.2283R`, PF `0.6086`; SHORT 465 fills at `-0.3916R`, PF `0.4209`.
24. **Symbol breakdown:** directional best expectancy was BTC `-0.3455R`, ETH `-0.2462R`, SOL `-0.1319R`. Every ordered pair in the relative-value best candidate was negative.
25. **Edge half-life:** conditional outcomes were measured at 1/3/6/12 tactical bars. The only mildly positive means appeared at longer horizons and stayed near 50% direction probability; executable versions were negative at their frozen 1h–2h holding designs.
26. **Entry model:** directional candidates use next tactical-bar open; cross-sectional candidates open equal-notional long/short legs at the next 15-minute open. No same-bar or future-flow entry is possible.
27. **Exit model:** directional candidates use a 1.5 ATR stop with 0.2% floor, 1.5R target, and fixed 12×5m or 8×15m time cap. Cross-sectional candidates use a frozen four-bar/one-hour time exit and ATR-percent risk normalization.
28. **Bootstrap uncertainty:** best directional hourly-cluster interval was `[-0.5000R, -0.3986R]`; best relative-value four-hour-cluster interval was `[-0.5010R, -0.4265R]`. Both exclude a positive clustered result.
29. **Whether directional flow alpha worked:** no. All 12 candidates failed expectancy, PF, cost, fold, drawdown, symbol, and uncertainty gates.
30. **Whether cross-sectional alpha was required:** yes, because Cycle A failed; it was the one explicitly permitted follow-up cycle.
31. **Cross-sectional results:** all 9 candidates were negative. Best BASE result was 2,472 fills, `-0.3087R`, PF `0.1988`, 20.35% WR, and 763.16R drawdown.
32. **Lead-lag findings:** BTC flow shocks showed no useful delayed ETH/SOL prediction. Across 6,358–6,513 observations per state, signed positive probabilities were about 45%–49%; means ranged from `-0.02352%` to `+0.00573%` across the reported horizons.
33. **Qualified challenger:** none. The experiment registry has 21 candidates, 21 Development rejections, and zero qualifiers.
34. **Shadow readiness:** feature and fail-closed integrity primitives are research-ready, but no challenger is eligible for Shadow promotion.
35. **Live-data collector status:** no production/live collector was enabled. The immutable public archive materializer exists; a separate append-only event collector for forward liquidation/raw-L2 evidence remains future work.
36. **Portfolio/risk readiness:** existing authoritative risk infrastructure remains available, but it was not exercised for a rejected challenger.
37. **Execution readiness:** modeled next-open costs are implemented. No candidate is ready for testnet, PAPER, or LIVE execution.
38. **Telegram functionality added:** none; without a qualified alpha, no flow value was mislabeled as a trade signal.
39. **Tests:** deterministic flow integrity, true aggressor/delta/CVD, completed aggregation, causality, candidate-roster, artifact-seal, cost monotonicity, DecisionQuality, and Shadow fail-closed tests were added.
40. **Full-suite result:** recorded in `TEST_BASELINE.md` after the final run, with the six established failures separated from any new regression.
41. **Remaining evidence risks:** one-venue Binance behavior may not transfer; event-level large-trade and liquidation history is absent; aggregate taker fields cannot reconstruct trade ordering or book state; modeled friction is not actual execution.
42. **Remaining technical risks:** a forward collector still needs reconnect/sequence/duplicate/resync coverage, append-only raw storage, and long-duration Shadow verification before any market-intelligence or signal integration.
43. **Exact next step:** stop bounded historical mining and preregister a forward-only public-data Shadow collection program for event trades, liquidations, and reconstructable book data; collect enough unseen calendar/cluster evidence before defining any new candidate.

## Protected-state attestation

- `FLOW_VALIDATION`: not accessed.
- `FLOW_BLIND`: not accessed.
- `BLIND_HOLDOUT`, derivatives Blind, and `LEGACY_SEEN_TEST`: not accessed.
- Production defaults: unchanged.
- PAPER/LIVE/copy execution: unchanged and not invoked.
- Private credentials and real orders: none.

FLOW / MICROSTRUCTURE EDGE STATUS: `NO VERIFIED FLOW EDGE`

LIVE STATUS: `RESEARCH READY ONLY`

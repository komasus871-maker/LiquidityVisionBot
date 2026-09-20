# Profitability-First Reconstruction — Final Bounded Report

## Executive result

The exact research engine is now practical and parity-certified, frozen F1 was
independently rejected, the research universe was expanded, and two bounded
challenger cycles were completed without opening Validation or Blind. The
honest result is:

`NO VERIFIED EDGE WITH CURRENT DATA/FEATURE SET`

Production strategy defaults are unchanged. LIVE was not enabled, no private
credentials were used, and no real or test orders were submitted.

## 1–3. Research engine

1. **Root cause:** expanding-prefix Pandas work repeatedly recomputed exact FVG,
   block, swing, indicator, normalization, and validation state, producing
   O(N²)-like overhead. FVG alone was about 66% of a representative profile.
2. **Speedup:** a complete 8,106-decision BTC confirmation replay fell from
   about 2h04m37s to 21.850s, approximately **342x**, with peak working set
   about 288.2 MB. A representative slice improved from 6.492s to 0.177s.
3. **Exact parity:** the 24-decision slice retained SHA-256
   `88dcfd8a850cfb94e01b42ea24c264d0aa1ec76227eec612a02241e6949f1a02`.
   Full BTC decisions/baseline/F1 hashes were respectively
   `61641825d0dbe3b1a165a9dfac7af57dbe2e1147d2114da89e72786c31b3f28a`,
   `ded4dbcb067932236d4d47241fe11ad8ea9eb9245a228dd603b86a71c1f866a8`,
   and `caa4047419995a76cbba08e6e5146bab2c7e94662d8f09b17653bacbfdafa3b8`.
   Decisions, plans, fills, exits, fees, MFE/MAE, and aggregates matched exactly.

## 4–10. Confirmation, data, and experiments

4. **F1:** `F1_NOT_CONFIRMED`. It had 124 fills/101 clusters,
   `-0.018310R` expectancy, one positive block, two catastrophic blocks,
   higher-cost `-0.054424R`, and stress `-0.090538R`. Validated final artifact
   SHA-256 is
   `eaee9ce810386b98a55feade9b34f73b9ed94df3d4ebdaff59b91703700e106c`.
5. **Expanded datasets:** immutable OKX BTC/ETH/SOL/XRP/DOGE swaps, 17,652
   closed `1h` candles each, 2022-09-01T00:00:00Z–2024-09-05T11:00:00Z.
6. **Symbols/timeframes researched:** the five symbols above, `1h` only.
   Other timeframes were not mixed into this experiment identity.
7. **Market state:** causal EMA direction/separation/slope, 6h/24h returns,
   path efficiency, ATR/volatility state, confirmed structure/BOS, sweeps,
   volume, displacement, range location, compression/range/transition/trend/
   volatile expansion, plus Cycle 2 prior-range breakout and ATR-normalized
   momentum/expansion. No future extrema or outcome label was used.
8. **Families:** Cycle 1 trend pullback LONG/SHORT and liquidity-sweep reversal
   LONG/SHORT; Cycle 2 range breakout LONG/SHORT and momentum expansion
   LONG/SHORT.
9. **Candidates:** 24 total, exactly three variants for each of eight families.
   Every configuration, including failures, is retained in machine-readable
   registries and listed in `STAGE_B_ALPHA_CHALLENGER_RESULT.md`.
10. **Rejections:** Cycle 1 failed mainly on the 60-fill/40-cluster minimum.
    Cycle 2 solved density but 11/12 variants were negative; the sole positive
    candidate failed expectancy, PF, fold, higher-cost, stress, neighbor, and
    bootstrap gates.

## 11–23. Best candidate and statistical evidence

11. **Best adequate-sample candidate:**
    `D3_MOMENTUM_EXPANSION_LONG:STRICT`, config `f2262e97c9019f72`.
12. **Development:** 526 fills; net expectancy-R `+0.016916R`; net-PnL PF
    `1.1572`; 47.338% WR; net PnL 3227.97 model price units; max nominal PnL
    drawdown 3607.65. It is rejected, not a challenger.
13. **Walk-forward/chronological folds:** 121 / 153 / 156 / 96 fills with
    `-0.0164R / +0.1709R / -0.0874R / -0.0170R`. Only one fold was positive.
14. **Validation:** not accessed because no Development candidate qualified.
15. **Blind:** neither the new Blind block nor the existing Phase 3B Blind was
    accessed. `LEGACY_SEEN_TEST` was not accessed for selection.
16. **WR:** 47.338% for the best adequate-sample candidate.
17. **Expectancy-R:** `+0.016916R`, far below the frozen `+0.15R` gate.
18. **PF:** 1.1572, below the frozen 1.25 gate.
19. **Drawdown:** 3607.65 nominal model-price units; not portfolio-capital DD,
    because position sizing was intentionally outside candidate discovery.
20. **MFE/MAE:** average 0.5055R / 0.4926R.
21. **Cost sensitivity:** base `+0.0169R`; higher cost `-0.0300R`; stress
    `-0.0971R`. Funding was unavailable and explicitly `NOT_MODELED`.
22. **Correlation-adjusted evidence:** 372 same-time/direction clusters, 98
    correlated clusters, largest positive cluster share 1.83%. The bootstrap
    95% expectancy interval was `[-0.1220R, +0.1756R]`.
23. **Confidence calibration:** not established. Raw strength was a transparent
    deterministic authority input by variant, not a calibrated probability;
    no promotion claim relies on it.

## 24–31. Architecture/readiness

24. **Entry:** family signal on a closed candle, mandatory
    `TradePlanIntegrity` and `DecisionQualityEngine`, then next-candle-open
    market fill with 0.03% base slippage.
25. **Exit:** 1.5 ATR stop (0.35% floor), 2 ATR target (1.2R floor), 120-bar
    maximum hold, conservative stop-first same-candle treatment, no tuned
    trailing/partial/breakeven variant.
26. **Portfolio/risk:** correlation clusters and symbol concentration were
    evaluated. No candidate reached the promotion point where capital sizing
    or portfolio execution could be authorized.
27. **SHADOW:** not started; there is no historically qualified challenger.
28. **PAPER:** existing production PAPER behavior remains unchanged; Alpha Lab
    is not connected to it.
29. **LIVE:** prior architecture remains unenabled. This program performed no
    LIVE certification, credentials connection, or order submission.
30. **Copy trading:** existing path remains unchanged; no challenger can issue
    follower intent.
31. **Telegram/product:** deliberately unchanged because the profitability
    milestone did not produce an edge worth surfacing.

## 32–36. Verification, risks, and next action

32. **Tests added:** vectorized-vs-legacy detector parity, sparse replay parity,
    prevalidated-pipeline parity, causal-cache future insensitivity, Stage B
    deterministic identities/budget, split guard, future insensitivity,
    DecisionQuality/plan authority, production-import isolation, and durable
    partition locks.
33. **Full suite:** `554 passed / 6 PRE_EXISTING failures / 0 new regressions`
    in 63.97s. Focused research verification: `26 passed`. The six failures are
    the known historical version/readiness/reversal expectations and are not
    caused by this work.
34. **Statistical risks:** only one Development period for Stage B, funding not
    modeled, correlated hourly signals, nominal rather than sized-portfolio DD,
    and no Validation/Blind result because no candidate earned access.
35. **Technical risks:** the worktree contains extensive preserved changes from
    earlier reconstruction phases; research modules are intentionally local and
    non-production. Exact parity is strongly tested, but deployment packaging
    remains outside this research stop condition.
36. **Next step:** locally acquire and integrity-certify synchronized public
    historical funding, open interest, and basis before preregistering any new
    program. No user authorization is needed for public read-only acquisition.
    Explicit user authorization would first be required for private exchange
    credentials, any real-money LIVE pilot, production-user deployment, or real
    copy trading; none is requested or recommended now.

## Final static review

- Production defaults remain unchanged; `prevalidated_research` defaults to
  `False`, and the accelerated path is an explicit research-only method.
- Production execution paths do not import either Stage B module.
- The causal cache activates only on an opt-in private DataFrame marker; normal
  production frames retain canonical behavior.
- Every candidate plan records `DecisionQualityEngine` authority and canonical
  `MARKET_NEXT_OPEN` provenance.
- Both cycle registries contain exactly 12 candidates; no post-hoc candidate
  exists.
- Artifact reconciliation `21e40bb64acff57a` reports `valid: true` with no
  errors and no protected partition access.

============================================================
FINAL EDGE STATUS
============================================================

NO VERIFIED EDGE

============================================================
LIVE STATUS
============================================================

RESEARCH READY ONLY

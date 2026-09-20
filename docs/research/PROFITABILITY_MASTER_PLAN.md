# Profitability-First Reconstruction — Master State

## Current state

- **Champion:** frozen Unified Analyzer control `84533946dfbbbf65`.
- **Rejected independent-confirmation candidate:**
  `F1_COUNTERTREND_REVERSAL_LONG` / `c49803034d94792e` is
  `F1_NOT_CONFIRMED`.
- **Active challenger:** none. Both bounded Stage B challenger-generation
  cycles completed without a Development qualifier.
- **Production strategy:** unchanged. No challenger is enabled in PAPER,
  SHADOW, LIVE, Telegram execution, or copy trading.
- **LIVE:** disabled by this program; no private credentials or real orders are
  used.
- **Biggest current edge blocker:** price-only OHLCV state separates market
  mechanisms but does not produce stable cost-adjusted expectancy. F1 failed
  independent confirmation; dense breakout/momentum challengers also failed
  temporal and cost robustness.
- **Next action:** acquire and integrity-certify synchronized historical
  funding, open interest, and basis data before a new preregistered program.
  No third OHLCV candidate cycle is permitted by the current budget.

## Verified immutable evidence

### Phase 3A census

- BTC/ETH/SOL, `1h`, 999 candles each, 2026-08-03 through 2026-09-13.
- Frozen result: 2,337 eligible decisions, 321 approved, 247 simulated fills.
- Aggregate expectancy `+0.1762R`, PF `1.2019`, but the final chronological
  window was `-0.1096R` with PF `0.8141`; therefore the baseline is unstable,
  not a verified persistent edge.

### Phase 3B/3C research data

- BTC `03265d42212ccd3c`, ETH `b3e5c9de0b38c779`, SOL
  `f51f8064a8b11fc1`.
- 8,999 valid closed `1h` candles per symbol from 2025-09-05T12:00:00Z
  through 2026-09-15T10:00:00Z.
- Fixed 55% Development / 20% Validation / 25% Blind design with protected
  settlement buffers. `LEGACY_SEEN_TEST` begins at 2026-08-03T01:00:00Z and
  is never new evidence.
- Blind and Legacy remain unopened for candidate selection.

### Independent F1 confirmation data

- BTC `a13e4c39db84cea9`, ETH `a7136b6d4ab94d0c`, SOL
  `7ca8216bd09d1f29`.
- 8,760 valid closed `1h` candles per symbol from 2024-09-05T12:00:00Z through
  2025-09-05T11:00:00Z.
- Frozen `CONFIRM_A/B/C` boundaries and the 60-fill / 30-cluster minimum remain
  exactly as registered in `MASTER_F1_CONFIRMATION_PREREGISTRATION.md`.
- BTC, ETH, and SOL canonical checkpoints are complete and preserved. The
  validated final artifact is `f1-confirmation-65b1dde9784282f9.json`, file
  SHA-256 `eaee9ce810386b98a55feade9b34f73b9ed94df3d4ebdaff59b91703700e106c`.
- F1 is `F1_NOT_CONFIRMED`: 124 fills, 101 clusters, `-0.018310R` aggregate
  expectancy, and two catastrophic blocks. Blind and Legacy remain locked.

## Candidate ledger

| Candidate | Evidence status | Result |
| --- | --- | --- |
| Unified Analyzer control `84533946dfbbbf65` | Development negative, Validation positive, short Phase 3A final window negative | Champion reference only; unstable |
| H1 SHORT admission | Development diagnostics | Rejected; useful gates remained negative |
| H2 confidence ceiling `ab5f8d32fd4b51a7` | Development + Validation | Rejected; failed preregistered PF-improvement gate |
| H3 profit protection | Development feasibility bound | Rejected; too few recoverable losses to repair expectancy |
| H4 regime/selectivity | Development diagnostics | Rejected; regime representation lacked useful separation |
| H5 symbol/cost robustness | Development diagnostics | No symbol disablement justified |
| F1 `c49803034d94792e` | Development positive; Validation 20 fills at `+0.451803R`; independent confirmation 124 fills at `-0.018310R` | `F1_NOT_CONFIRMED`; frozen and rejected, no Blind access |
| Stage B Cycle 1 (12 variants) | New BTC/ETH/SOL/XRP/DOGE Development only | All rejected; 0–28 fills except one 16-fill broad long at `+0.3522R`; no minimum-sample qualifier |
| Stage B Cycle 2 (12 variants) | Same immutable Development, targeted breakout/momentum features | All rejected; 11 negative-expectancy variants; best adequate-sample variant `f2262e97c9019f72` only `+0.0169R`, PF `1.1572`, cost/temporal/bootstrap failures |

No failed experiment is deleted or renamed as success. The final Stage B
reconciliation is `stage-b-reconciliation-21e40bb64acff57a.json` and reports
no artifact errors or protected-partition access.

## Expanded Stage B data

- Public OKX BTC/ETH/SOL/XRP/DOGE swaps, `1h`, exactly 17,652 closed candles
  per symbol from 2022-09-01T00:00:00Z through 2024-09-05T11:00:00Z.
- Content IDs: BTC `d800796de20eaa9e`, ETH `31ff8dfcf9ecbd4b`, SOL
  `babf2f6205a3d619`, XRP `3f36cb73f7d7cc84`, DOGE
  `e3fcecb864cbfa2c`.
- Candidate construction loaded only through the Development settlement tail
  `2023-09-07T23:00:00Z`. New Validation and Blind outcome blocks were not
  accessed; the pre-existing Phase 3B Blind and Legacy partitions also remain
  untouched.

## Bounded Stage B result

Cycle 1 tested trend-pullback LONG/SHORT and sweep-reversal LONG/SHORT. It
failed primarily on signal and cluster sample size. Cycle 2 was preregistered
before outcomes and tested range-breakout and momentum-expansion LONG/SHORT.
It produced adequate samples but no robust edge. The strongest adequate-sample
result, strict momentum-expansion LONG `f2262e97c9019f72`, had 526 fills,
47.34% WR, `+0.016916R`, PF `1.1572`, bootstrap 95% interval
`[-0.12198R, +0.17561R]`, only one positive chronological fold, higher-cost
expectancy `-0.03005R`, and stress expectancy `-0.09714R`.

The bounded conclusion is therefore:

`NO VERIFIED EDGE WITH CURRENT DATA/FEATURE SET`

## Research-engine milestone

The original expanding-prefix path was profiled and optimized without changing
economic semantics. A complete 8,106-decision BTC confirmation now finishes in
about 21.85 seconds rather than about 2h04m37s (approximately 342x faster) with exact
serialized decision and outcome hashes. Cross-symbol BTC-context parity is
also exact. Details and automated parity coverage are recorded in
`RESEARCH_ENGINE_ACCELERATION.md`.

## Alpha Challenger protocol after F1

If F1 is `F1_NOT_CONFIRMED` or `F1_INSUFFICIENT_NEW_EVIDENCE`, Stage B starts
without opening Blind or Legacy. Cycle 1 is bounded to existing causal features
and three to five separately motivated alpha families, with at most three
variants per family. LONG/SHORT mechanisms remain independent, every candidate
must pass DecisionQuality, and all selection remains inside Development before
one candidate can enter Validation.

If F1 is `F1_CONFIRMED`, its result and hashes are frozen first. Only then may
the existing untouched Blind be evaluated once for the original frozen
baseline versus unchanged F1. There are no post-Blind changes.

## Readiness

- **Validation:** F1 independent confirmation completed and rejected. Stage B
  Validation was not opened because neither cycle produced a qualifier.
- **Blind:** protected / not accessed.
- **Legacy:** protected / not accessed for candidate selection.
- **Shadow:** not started; no historically qualified challenger yet.
- **Forward evidence:** none for F1 or a new challenger.
- **PAPER:** existing production behavior unchanged; reconstruction work has
  not promoted experimental alpha.
- **LIVE readiness:** research-ready infrastructure only; no verified alpha,
  no automatic enablement, and no real orders.
- **Copy trading:** no experimental strategy is connected to follower
  execution.

## Derivatives alpha reconstruction (2026-09-19)

The next bounded cycle added a research-only, venue-explicit derivatives stack using official Binance public archives. Materialization `8886c1cd8d5e0b97` covers BTC/ETH/SOL from 2024-07-01 through 2026-06-30 with OI, settled funding, perpetual/index/mark/spot prices, and basis. Production defaults and the OKX runtime path were not changed.

The primary 1h cycle evaluated exactly twelve preregistered variants; all were negative under BASE costs. A single justified 4h cycle tested nine separately preregistered horizon variants. Its best candidate, `a6c42a84d6a21e52`, reached only `+0.011520R`, PF `1.0274`, with negative HIGH/STRESS results, unstable folds, 41.56R drawdown, and a bootstrap interval spanning zero. It was rejected. Validation and Blind remained sealed. The result is `NO VERIFIED DERIVATIVES EDGE`; further mining is outside the bounded protocol.

## Flow / microstructure bounded cycle (2026-09-19)

The next program acquired genuinely new information: exchange-reported Binance USD-M taker-buy aggregates at 5-minute resolution, combined with same-venue OI and settled funding. The immutable data contract prevents candle-color CVD, future-interval use, missing-as-zero liquidation semantics, and silent venue-transfer assumptions.

Cycle A tested exactly twelve preregistered directional flow variants. All failed Development. Cycle B then tested the one allowed set of nine equal-notional BTC/ETH/SOL relative-value variants; all also failed Development, while BTC-to-ETH/SOL lead-lag remained near chance. No candidate unlocked Validation or Blind.

Bounded historical alpha mining is now stopped. Production defaults remain unchanged. A future program must add genuinely unseen information through a preregistered append-only public event collector and Shadow evidence window; it must not tune the 21 rejected flow variants. Full evidence is in [FLOW_ALPHA_MASTER.md](FLOW_ALPHA_MASTER.md) and [FLOW_ALPHA_RESULTS.md](FLOW_ALPHA_RESULTS.md).

## Forward microstructure program — infrastructure operational

The required new-information path is now implemented as a standalone, public-data, Shadow-only subsystem. It captures exact venue provenance, receive-order raw events, fail-closed L2 state, causal flow/book/liquidation/derivatives/cross-venue features, ten preregistered candidate variants, conservative latency/cost fills, separated future labels, data-quality diagnostics, and deterministic replay.

A three-venue engineering certification passed with zero final book gaps and exact feature/decision replay. Certification is not profitability evidence. The program now waits for the frozen minimum forward duration and samples; it does not reopen historical mining, change production strategy defaults, enable LIVE, or submit orders. See [FORWARD_ALPHA_PREREGISTRATION.md](FORWARD_ALPHA_PREREGISTRATION.md) and [FORWARD_ALPHA_LAB.md](FORWARD_ALPHA_LAB.md).

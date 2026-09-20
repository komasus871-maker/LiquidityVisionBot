# Flow Cycle B: Cross-sectional preregistration

Status: **FROZEN BEFORE OUTCOME EVALUATION**
Cycle: `B_CROSS_SECTIONAL`
Materialization: `fc1879232f3149d7`
Source venue: `BINANCE_UM`

Cycle A rejected all 12 directional candidates on `FLOW_DEV`; neither `FLOW_VALIDATION` nor `FLOW_BLIND` was accessed. This document freezes the one permitted relative-value cycle before its outcomes are computed.

## Protected samples

Only `FLOW_DEV` (`2023-01-01 00:00 UTC` through `2025-09-30 23:55 UTC`) may be loaded. The immutable Development hashes are:

- BTCUSDT: `f90ba2112f1a50283d2a2edd1ab073c6b86c0a3f4e8e24ffd2c2da9656de8116`
- ETHUSDT: `d0e404d4fc993b9156975606d5b21cf15dc75293a0b999f411bf364d2dc77479`
- SOLUSDT: `15c027ea83d531d6499eb79c5579c65243dcff8fe7ac0849df9bbd76e9721531`

`FLOW_VALIDATION` and `FLOW_BLIND` remain sealed. The prior derivatives Blind interval and `LEGACY_SEEN_TEST` remain inaccessible.

## Construction and causal timing

All candidates use closed 15-minute bars built only from completed certified 5-minute Binance USD-M aggregates. At each decision time, the strategy ranks exactly BTC, ETH, and SOL, then opens an equal-notional long/short pair at the next 15-minute open. It exits at the close of the fourth bar after the decision (one-hour tactical horizon). There is no portfolio beta estimate fitted from future data.

Risk normalization is `max(1.5 * mean(long ATR%, short ATR%), 0.003)`. Reported R is equal-notional pair return after two-sided fees, entry/exit friction on both legs, and actual funding settlements, divided by that frozen risk unit. All candidates must pass `DecisionQualityEngine`; research artifacts have no execution authority.

## Frozen families

1. `RV1_RELATIVE_FLOW_CONTINUATION`: long the asset with the strongest real delta z-score and positive 45-minute price movement; short the weakest real delta z-score with negative 45-minute movement.
2. `RV2_PRICE_FLOW_MISMATCH_REVERSION`: rank the negative of price-strength minus delta-z mismatch; require one-bar reversal confirmation, then long the low-price/high-flow mismatch and short the high-price/low-flow mismatch.
3. `RV3_OI_FLOW_DISPERSION`: rank delta z-score plus `0.5 * OI-change z-score`; require valid OI, directionally aligned price, and reject the most adverse 5% funding tail on either leg.

Each family has exactly three variants. A signal requires the long score to be at least the threshold, the short score at most the negative threshold, and a cross-sectional score gap of at least twice the threshold.

| Family | BROAD | BASE | STRICT |
|---|---:|---:|---:|
| RV1 | `c70fca23e8419dba` / 0.75 | `ae27e26c3504d7e8` / 1.00 | `d25ff7f1c6da3e52` / 1.25 |
| RV2 | `26bd0e5f2a75d380` / 0.75 | `b4c1c6cc43f775f6` / 1.00 | `eaa4719cf668958b` / 1.25 |
| RV3 | `cafb8ccbd391bc6a` / 0.75 | `0e75190a5fc45a6e` / 1.00 | `1b1d5f84d9931b53` / 1.25 |

No fourth family, replacement candidate, or post-outcome threshold change is permitted.

## Frozen costs

- BASE: 5 bp fee per side, 5 bp entry friction, 3 bp exit friction.
- HIGH: 7.5 bp fee per side, 9 bp entry friction, 7 bp exit friction.
- STRESS: 10 bp fee per side, 15 bp entry friction, 12 bp exit friction.

The same cost is applied independently to each equal-notional leg. Funding uses actual aligned settlements and is never imputed as zero when its status is invalid.

## Development folds and dependence

The four chronological folds remain `2023-01-01..2023-08-31`, `2023-09-01..2024-04-30`, `2024-05-01..2024-12-31`, and `2025-01-01..2025-09-30`. Dependence is measured using four-hour UTC event clusters and a 1,000-resample cluster bootstrap. BTC flow lead-lag into ETH/SOL is descriptive only and cannot create a replacement candidate.

## Frozen promotion gates

A candidate qualifies for `FLOW_VALIDATION` only if every gate passes:

- at least 200 fills and 120 four-hour clusters;
- BASE expectancy at least `+0.04R` and PF at least `1.15`;
- HIGH and STRESS expectancy positive with PF above 1;
- at least three of four folds nonnegative and no fold below `-0.08R`;
- maximum drawdown no greater than `20R`;
- at least two ordered pair configurations have positive expectancy;
- cluster-bootstrap lower bound above `-0.02R`;
- all three neighboring variants have at least 100 fills;
- all causal, genuine-flow, venue, equal-notional, and DecisionQuality integrity checks pass.

If multiple candidates qualify, the frozen ordering is BASE expectancy, cluster count, then bootstrap lower bound. Only the first may be frozen for Validation. If none qualifies, Validation and Blind remain sealed and bounded historical alpha mining stops.

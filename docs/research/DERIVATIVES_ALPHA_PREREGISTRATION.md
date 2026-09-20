# Derivatives Alpha Preregistration

Frozen before forward-return or trade outcomes were computed. Identity: `derivatives-alpha-primary-1h-v1`.

## Dataset and protected chronology

Provider: official Binance public archive, USD-margined `BTCUSDT`, `ETHUSDT`, and `SOLUSDT`, 1h decisions. Materialization covers exactly 2024-07-01 00:00 through 2026-06-30 23:00 UTC. Content hashes are appended to the immutable materialization manifest before Development outcome access.

Certification completed before outcome access as materialization `8886c1cd8d5e0b97` (manifest SHA-256 `6b653539e94231e434108d2b9a40d90dc6cf551a655b0bf83ed4a8580293bf1d`). Per-split SHA-256 values are stored in the machine-readable preregistration and materialization manifest. Each Development symbol has 10,968 hourly rows, Validation has 3,624, and Blind has 2,928; all source and dataset hashes reconciled.

| Role | Start | End | Access rule |
|---|---|---|---|
| `DERIV_DEV` | 2024-07-01 00:00Z | 2025-09-30 23:00Z | family discovery and bounded variants only |
| `DERIV_VALIDATION` | 2025-10-01 00:00Z | 2026-02-28 23:00Z | only after one candidate is frozen |
| `DERIV_BLIND` | 2026-03-01 00:00Z | 2026-06-30 23:00Z | only after Validation qualification; one access |

Development folds are 2024-Q3, 2024-Q4, 2025-Q1, 2025-Q2, and 2025-Q3. Validation and Blind boundaries cannot move. The older OHLCV Validation, `BLIND_HOLDOUT`, and `LEGACY_SEEN_TEST` are not part of this experiment and remain prohibited for selection.

## Frozen mechanics

Features use only observations available at or before the decision. Entry is next-hour open with market costs. Initial risk is 1.5 ATR(14), with a 0.35% price floor; target is 1.5R; maximum holding is 24 bars; stop/target collision is stop-first. The existing deterministic historical replay supplies closed-candle, next-open, gap, and barrier semantics. Outcomes are independently simulated per eligible decision; cluster evidence groups same decision timestamp, direction, and family.

Costs:

| Model | Fee per side | entry slippage/spread | exit slippage/spread | funding |
|---|---:|---:|---:|---|
| BASE | 5 bp | 4 bp | 4 bp | actual settlements |
| HIGH | 7.5 bp | 7 bp | 7 bp | actual settlements |
| STRESS | 10 bp | 12 bp | 12 bp | actual settlements |

Funding used as a feature is separate from funding charged during holding and is never double-counted.

## Frozen families and variants

There are exactly four mechanism families and three variants (`BROAD`, `BASE`, `STRICT`) per family; no additional primary-cycle candidate may be created after Development is viewed.

1. `D1_LEVERAGED_TREND_LONG` and independently `D1_LEVERAGED_TREND_SHORT`: aligned six-hour price/OI expansion, non-extreme adverse funding/basis, one-hour continuation confirmation.
2. `D2_DELEVERAGING_REVERSAL`: extreme six-hour move with OI contraction and one-hour reversal confirmation; long and short are evaluated separately in attribution.
3. `D3_CROWDING_REVERSAL`: joint funding/basis crowding extreme with elevated OI and one-hour reversal confirmation; long and short are independent.

Variant thresholds use causal rolling percentiles. `BROAD/BASE/STRICT` core percentiles are 60/70/80 and extreme percentiles are 80/85/90. No future-return label enters these predicates. Development conditional-return tables may reject mechanisms but may not manufacture replacement families.

## Promotion gates frozen before Validation

A Development candidate must satisfy every gate:

- at least 120 nominal fills and 60 clusters;
- aggregate BASE net expectancy at least +0.05R and PF at least 1.15;
- HIGH and STRESS net expectancy positive and PF above 1.00;
- at least three of five folds have nonnegative expectancy, no fold is below -0.10R, and no single fold supplies more than 60% of positive net R;
- maximum drawdown no worse than 25R;
- both at least two symbols and the declared directional mechanism contribute; no tiny subgroup supplies the result;
- deterministic cluster/block-bootstrap expectancy lower 95% bound above -0.03R;
- causal alignment, integrity, and replay checks all pass.

At most one candidate, ranked by Development BASE expectancy then cluster N then lower bootstrap bound, may be frozen for Validation. Validation qualification requires at least 50 fills and 30 clusters, BASE expectancy above +0.03R, PF above 1.10, HIGH and STRESS expectancy positive, drawdown no worse than 15R, and bootstrap lower bound above -0.05R. No threshold may be lowered.

Only a Validation-qualified frozen candidate may open `DERIV_BLIND`, exactly once and with no post-Blind modification. If none qualifies, Blind remains sealed. One second derivatives cycle is allowed only after a documented diagnosis, and must be a separately preregistered liquidation/flow source or timeframe—not more threshold mining.

# Flow Alpha Preregistration

Frozen before FLOW Development outcomes. Identity: `flow-alpha-directional-v1`.

## Immutable design

Universe: Binance USD-M `BTCUSDT`, `ETHUSDT`, `SOLUSDT`. Base data: genuine exchange-reported 5m taker-buy flow, OI, and settled funding. Tactical horizons are 5m and 15m; one-hour price context is causal and closed. No liquidation or L2 feature enters a candidate.

The materialization window is 2023-01-01 through 2026-08-31, excluding 2026-02-01 through 2026-06-30 from outcome access. This exclusion preserves the older derivatives protected Blind interval.

| Role | Boundary | Rule |
|---|---|---|
| `FLOW_DEV` | 2023-01-01 00:00Z–2025-09-30 23:55Z | conditional information, ablation, variants |
| `FLOW_VALIDATION` | 2025-10-01 00:00Z–2026-01-31 23:55Z | one frozen directional candidate only |
| protected gap | 2026-02-01–2026-06-30 | inaccessible |
| `FLOW_BLIND` | 2026-07-01 00:00Z–2026-08-31 23:55Z | one Validation-qualified finalist only |

Dataset hashes are appended to the machine preregistration after materialization and before outcome access.

Certification completed before outcomes as materialization `fc1879232f3149d7` (manifest SHA-256 `4c0bbd0bacc6aaaaa7a96fed6967af9e7ac524dc6bb9e15d0b08a19810a94fcf`). Each symbol has 289,152 Development rows, 35,424 sealed Validation rows, and 17,856 sealed Blind rows. All split files passed chronology, exact-frequency, aggressor-volume, nonfinite, and hash checks. Short OI source gaps remain explicit (BTC 129 rows, ETH 129, SOL 142); flow-only rows remain valid and OI-dependent predicates fail closed.

## Frozen directional families

Exactly four mechanism families, three `BROAD/BASE/STRICT` variants each:

1. `F1_FLOW_CONTINUATION_5M`: 15m price progress, aligned rolling delta, and closed 1h trend.
2. `F2_CVD_DIVERGENCE_REVERSAL_15M`: price/CVD disagreement with a completed 15m reversal confirmation.
3. `F3_ABSORPTION_REVERSAL_5M`: extreme normalized aggression with weak price progress followed by causal reversal confirmation.
4. `F4_OI_FLOW_SQUEEZE_15M`: aligned OI expansion, flow shock, price direction, and non-adverse funding state.

LONG and SHORT predicates are independently declared, not automatic sign mirrors. Core percentile thresholds are 70/75/80 and extreme thresholds are 85/90/95 for BROAD/BASE/STRICT. No other primary-cycle variant may be created.

## Replay and costs

Entry is next tactical-bar open. Stop is 1.5 ATR(14) with a 0.20% floor; target is 1.5R. Maximum holding is 12 bars for 5m and eight bars for 15m. Stop/target collision is conservative stop-first. Funding is counted from actual settlements once where a trade crosses an event.

Short-horizon cost models per side are:

- BASE: 5 bp fee; 5 bp entry spread/slippage/latency; 3 bp exit friction.
- HIGH: 7.5 bp fee; 9 bp entry friction; 7 bp exit friction.
- STRESS: 10 bp fee; 15 bp entry friction; 12 bp exit friction.

## Directional promotion

Before `FLOW_VALIDATION`, every gate is required: at least 300 fills and 120 timestamp/direction/family clusters; BASE expectancy at least +0.04R and PF at least 1.15; HIGH and STRESS expectancy positive with PF above 1; at least three of four chronological Development folds nonnegative and none below -0.08R; maximum drawdown at most 30R; two positive symbols; no direction or symbol tiny-subgroup dependency; cluster-bootstrap lower 95% expectancy above -0.02R; causal alignment, DecisionQuality, venue provenance, and canonical replay valid; and no razor-edge neighborhood.

At most one candidate can be frozen. Validation requires at least 100 fills/50 clusters, expectancy above +0.03R, PF above 1.10, positive HIGH/STRESS expectancy, drawdown at most 20R, and bootstrap lower bound above -0.04R. Only a Validation qualifier may access Blind once.

## Explicit failure branch

If all twelve directional variants fail Development, no directional Validation is opened. One separately frozen cross-sectional cycle may test relative flow/price/OI dispersion at 15m using the same FLOW_DEV hashes. It may contain at most three families and three variants each. If that cycle also fails Development, historical flow-alpha mining stops.

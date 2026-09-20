# Derivatives Alpha Results

Status: bounded program complete. No candidate qualified for Validation. `DERIV_VALIDATION` and `DERIV_BLIND` remain sealed.

## Data and integrity

The official Binance public archive supplied a venue-coherent 24-month BTCUSDT/ETHUSDT/SOLUSDT USD-margined dataset. It includes perpetual, mark, index, premium-index, spot, settled funding, OI, long/short positioning ratios, and taker ratios. Materialization `8886c1cd8d5e0b97` reconciles to its source and split hashes with no error. Each symbol has 10,968 Development, 3,624 sealed Validation, and 2,928 sealed Blind hourly rows.

Provider venue is explicit on every row and experiment. These results are Binance historical evidence only. They do not establish transferability to OKX or BingX. The preregistered overlapping cross-venue transferability check would be required after historical qualification and before production promotion; because no candidate qualified, that check was not triggered.

Liquidation history, full trade flow/CVD, and order-book history were not certified and were not used. Archived taker and positioning ratios were retained for descriptive audit but excluded from primary predicates. Missing data were never interpreted as zero.

## Primary 1h cycle

Exactly twelve preregistered variants across four mechanism identities were evaluated using Development only. Every signal passed `DecisionQualityEngine` before canonical next-open replay. Costs included two-sided fees, entry and exit friction, and actual funding settlement events counted once.

All twelve variants were negative under BASE costs. The least-negative adequate-sample primary candidate was `D1_LEVERAGED_TREND_SHORT:BROAD` (`57ed5f01da27b4e3`): 685 fills, 624 clusters, `-0.102365R` expectancy, PF `0.8426`, and 91.31R drawdown. No primary candidate approached the frozen promotion contract.

Development-only conditional labels showed that D1 continuation and D2 deleveraging states were generally adverse at short horizons but some had positive mean signed returns at 24 hours. D3 crowding reversal remained directionally adverse through 24 hours. This supported exactly one separate 4h horizon-mismatch cycle; it did not justify tuning the rejected 1h thresholds.

## Single allowed 4h cycle

The second-cycle preregistration froze nine variants: D1 LONG, D1 SHORT, and D2 reversal, each BROAD/BASE/STRICT. It used the same data hashes and splits, UTC-aligned causal 4h aggregation, next-4h-open entry, six-bar maximum holding, and unchanged costs/gates.

The best rejected candidate was `C2_4H_D1_LEVERAGED_TREND_LONG:BASE` (`a6c42a84d6a21e52`):

| Evidence | Result |
|---|---:|
| nominal fills / clusters | 694 / 504 |
| BASE WR | 46.97% |
| BASE expectancy / PF | +0.011520R / 1.0274 |
| BASE net R / max drawdown | +7.995R / 41.562R |
| HIGH expectancy / PF | -0.032678R / 0.9263 |
| STRESS expectancy / PF | -0.092958R / 0.8046 |
| bootstrap expectancy 95% interval | [-0.096076R, +0.136057R] |
| mean MFE / MAE | 0.6048R / 0.4825R |

Only 2024-Q4 and 2025-Q3 were positive. 2025-Q2 was `-0.124514R`, breaching the non-catastrophic-fold rule. Symbol expectancy was BTC `+0.027125R`, ETH `+0.014474R`, and SOL `-0.010302R`. It failed the frozen expectancy, PF, HIGH/STRESS, fold, drawdown, and uncertainty gates. Nearby BROAD and STRICT variants were also non-qualifying, so there is no stable parameter plateau.

All other cycle-2 adequate-sample variants were negative. The 19-fill strict short result was positive but failed minimum sample/cluster requirements and is not treated as evidence.

## Protected splits and readiness

- Development qualifiers: 0.
- Frozen Validation candidates: 0.
- Validation access: no.
- Blind access: no.
- Legacy seen-test access: no.
- Production strategy/default changes: none.
- Shadow/PAPER/LIVE/copy activation: none.
- Real orders or private credentials: none.

Confidence calibration was not fit because no candidate qualified. Portfolio simulation, historical challenger integration, Shadow promotion, Telegram signal cards, and cross-venue transferability evaluation were not triggered. The existing risk/execution contracts remain unchanged.

Machine evidence is in `research_artifacts/derivatives_alpha/`: materialization, primary Development, final 21-experiment registry `b119f035b08cc3b7`, cycle-2 Development, preregistrations, and reconciliation `bfb2c42bcd183dd4` (`valid: true`, no errors).

Verification: 88 focused tests passed. The full suite finished at 567 passed and the same six documented pre-existing failures, with zero new regression. Static compilation passed; no handler/core/database/production service imports the derivatives research modules; both Development runners lack a protected-split loader; and candidate admission invokes `DecisionQualityEngine.authorization`.

The bounded result is `NO VERIFIED DERIVATIVES EDGE`. No further derivatives threshold or timeframe mining is authorized under this program.

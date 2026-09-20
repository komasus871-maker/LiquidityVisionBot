# Forward Microstructure Alpha Preregistration

Status: **FROZEN BEFORE FORWARD OUTCOME INSPECTION**
Program: `forward-microstructure-alpha-v1`
Frozen at: `2026-09-20T10:42:36.5655344Z`
Mode: `SHADOW_ONLY`
Execution authority: `false`

## Evidence boundary

This is a new forward evidence generation. Historical OHLCV, derivatives, directional-flow, relative-value, and lead-lag candidates remain rejected and cannot be rescued by this program. Candidate V1 evidence begins only after the collector receives events following the freeze timestamp above. Earlier observations, parser fixtures, connectivity certification, and historical artifacts cannot count as candidate outcomes.

Raw events preserve exchange time and local receive time. Features use receive-time causal availability. Outcome labels are written later to a separate append-only table and are marked `feature_eligible=false`. Any parameter or semantic change creates a new candidate identity and a new first-evidence timestamp.

## Frozen universe and capabilities

Universe: BTCUSDT, ETHUSDT, SOLUSDT perpetual futures on Binance, OKX, and BingX.

- Binance: aggressor-labelled aggregate trades, incremental L2 with REST snapshot, public force orders, mark/index/funding WebSocket, public OI polling.
- OKX: taker-side trades, sequenced incremental L2, public liquidation orders, mark/index/OI/funding WebSocket. Since 2026-06-23 its JSON book checksum is fixed to zero and ignored; integrity uses strict `seqId/prevSeqId` continuity as required by the current venue contract.
- BingX: aggressor-labelled trades, bounded top-20 snapshots, mark/index/funding and public OI; liquidation is unavailable and L2 is explicitly `SNAPSHOT_ONLY`.

A candidate requiring unavailable or invalid data emits no decision.

## Frozen families and variants

Exactly five families and two variants per family are permitted.

| ID | Family | Variant | Frozen parameters |
|---|---|---|---|
| `a8ccc1b1ded41bef` | M1 flow/book momentum | STANDARD | 5s normalized delta 0.20, depth imbalance 0.15, max spread 4 bps |
| `6e05b968e61e801f` | M1 flow/book momentum | STRICT | 5s normalized delta 0.30, depth imbalance 0.25, max spread 3 bps |
| `c119789e70cd968a` | M2 absorption reversal | STANDARD | 30s normalized delta 0.30, max price progress 2 bps, opposite depth imbalance 0.10 |
| `062fe62e6c775320` | M2 absorption reversal | STRICT | 30s normalized delta 0.40, max price progress 1 bp, opposite depth imbalance 0.20 |
| `1af9004c5fb37aa9` | M3 liquidation exhaustion | STANDARD | 60s forced notional $250k, absolute 5s flow no greater than 0.10, observed OI decline |
| `1bd7009a9c433bc7` | M3 liquidation exhaustion | STRICT | 60s forced notional $1m, absolute 5s flow no greater than 0.05, observed OI decline |
| `728d4faaacc69b80` | M4 liquidity-vacuum breakout | STANDARD | opposite near-depth no greater than $50k, 5s delta 0.15, max spread 5 bps |
| `89e868a4531e3449` | M4 liquidity-vacuum breakout | STRICT | opposite near-depth no greater than $20k, 5s delta 0.25, max spread 4 bps |
| `f8926793803c30ca` | M5 cross-venue lead/lag | STANDARD | leader 5s move 3 bps, laggard gap 2 bps, at least 2 valid venues |
| `804922d97a11cce0` | M5 cross-venue lead/lag | STRICT | leader 5s move 5 bps, laggard gap 3 bps, all 3 venues valid |

M1 follows aligned aggressive flow and book pressure. M2 fades aggression that produces little progress against opposite-side replenishment. M3 requires a real liquidation burst, an observed OI decline, and exhausted short-horizon flow. M4 follows aggression into genuinely thin observable opposite-side liquidity. M5 observes a causal venue move and a still-displaced lagging venue. No candidate has order authority.

## Frozen labels and execution model

Research labels are signed returns, MFE, and MAE at 1s, 5s, 15s, 30s, 1m, 5m, 15m, 30m, and 1h. They are created only after their horizon elapses.

Shadow market fills use the observable touch plus an adverse latency penalty and a 5 bps one-way taker fee assumption. The frozen latency scenarios are 50ms `LOW_LATENCY`, 250ms `NORMAL_LATENCY`, and 1,000ms `STRESS_LATENCY`. Shadow never gets a fill inside the observable spread and never submits an order.

Each label horizon also closes the hypothetical position against the then-observable opposite touch with the same adverse latency model and a second 5 bps taker fee. Gross return, total fees, net return, and net USD PnL are stored for every latency scenario. The primary fixed-horizon comparison is 5 minutes (`300000ms`); all other horizons are predeclared half-life diagnostics and cannot be selected post hoc as a replacement primary result.

## Frozen promotion requirements

Every requirement must pass before a candidate may leave forward Shadow research:

- at least 30 calendar days of unseen evidence;
- at least 500 decisions and 250 independent event clusters;
- at least three volatility states and three directional states;
- net expectancy at least `+0.04R` and PF at least `1.20`;
- maximum drawdown no greater than `25R`;
- positive HIGH and STRESS cost expectancy;
- positive NORMAL and STRESS latency expectancy;
- positive evidence on at least two venues;
- reproducible event replay with identical feature and decision identities;
- no book-integrity, clock, gap, or feature-label contamination defect.

Win rate is secondary. No requirement may be weakened after outcomes are observed.

## Stop and genealogy rules

No daily threshold optimization is allowed. Changing a threshold, horizon, state definition, fill assumption, or feature creates V2 and prevents reuse of V1 observations as pristine V2 evidence. The first review occurs only when minimum calendar duration and sample requirements are both met. Until then every registry result remains `FORWARD_EVIDENCE_PENDING`.

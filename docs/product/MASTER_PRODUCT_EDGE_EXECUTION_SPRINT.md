# Master product, edge, and execution sprint

Status: repository implementation complete; production smoke acceptance awaits deployment.

## Authority and evidence boundaries

Scanner output remains `MARKET_ALERT` with `economic_authority=false`. Scanner
labels and attribution are `RESEARCH_SHADOW_EVIDENCE`; they do not create a
trade signal, PAPER authorization, candidate promotion, or LIVE authorization.
DecisionQuality and the unified execution lifecycle remain the only downstream
authorities. Frozen candidate identities, historical results, and sealed
forward profitability were not read or changed.

## Product changes

- Terminal authentication now has a bounded Telegram bridge load, signed
  init-data exchange, 15-minute signed session, bounded bootstrap, safe reason
  codes, Retry, Close/Back, and structured diagnostics.
- Scanner has an explicit Home route for the visible button and bare command,
  16 first-class product views, actual operational statistics, explainable
  ranked records, refresh/watch/order-flow actions, and matching Terminal view
  selection.
- The scanner feedback ledger freezes decision-time JSON and matures causal
  sampled-path labels at 1/3/5/15/30/60/240 minutes with modeled costs. The
  original event is never updated by later data.
- Economic observability separates scanner evidence, frozen hypothesis states,
  simulated PAPER accounting, and infrastructure expense. Missing cost inputs
  stay visibly unconfigured; PAPER value is never labeled real profit.
- Venue state is normalized to `CONNECTED`, `WARMING`, `HEALTHY`, `DEGRADED`,
  `STALE`, or `DISCONNECTED`; cross-venue features continue to require fresh
  independent venues.

## Profitability roadmap

| Stage | Entrance criteria | Tests and metrics | Failure conditions | Promotion criteria |
|---|---|---|---|---|
| A — Production Product Acceptance | Repository verification clean; migration v2 ready | Real Telegram Android auth/bootstrap, all menu paths, workers, PostgreSQL, R2, restart and resource smoke | Any dead route, infinite wait, OOM, migration contention, authority leak | Every acceptance row passes in deployed production; LIVE stays locked |
| B — Scanner Evidence Accumulation | A passes; immutable snapshots and label scheduler active | Coverage/freshness, episode counts, label completion, gaps, data quality; at least 30 independent labeled samples per reported cohort | Missing/stale sources, future leakage, concentration, label drift | Predeclared cohort inventory has adequate causal samples and integrity evidence |
| C — Strategy Discovery | B evidence frozen; hypothesis written before outcomes are selected | Development-only causal replay, temporal folds, costs, MFE/MAE, overlap and concentration | Post-hoc filters, brute-force search, unstable folds, negative cost stress | Small preregistered roster qualifies under development gates only |
| D — Candidate Validation | Candidate/config/identity and thresholds frozen | Untouched chronological validation, bootstrap CI, PF/expectancy/drawdown, regime/temporal stability, 1x/2x costs | Minimum sample missed or any mandatory gate fails | All preregistered validation gates pass without modification |
| E — PAPER Qualification | Validated candidate frozen; no blind modification | Unified PAPER lifecycle, partial fills, SL/TP, fees/funding/slippage, recovery, reconciliation, portfolio heat | Material replay-to-PAPER drift, negative net expectancy, operational errors | Minimum forward sample and duration plus stable net economics and bounded risk |
| F — Exchange Connectivity Qualification | E passes; explicit operator setup; no withdrawal permission | Read-only balances/positions/orders, permissions, symbol rules, WS/REST reconciliation, stale/outage gates, idempotent dry-run | Credential/permission leak, mismatch, stale state, retry ambiguity | Repeated sandbox/VST certification and fail-closed recovery pass |
| G — Tiny LIVE Qualification | Separate explicit authorization after F; kill switch tested | Strict notional/risk caps, real fill/reconciliation/slippage, daily/weekly loss gates | Any unexplained divergence, risk breach, outage, negative operational evidence | Predeclared tiny-LIVE duration/sample and net-after-cost gates pass |
| H — Copy-Trading Qualification | G stable; master decision authority frozen | Per-subscriber sizing, min notional/precision, partial fills, rejection/divergence, lifecycle sync | Drift, duplicate order, subscriber risk or reconciliation breach | All subscribers remain within configured risk and divergence thresholds |
| I — Scaling | H qualifies; portfolio capacity estimate frozen | Incremental size tests, impact/slippage, correlation, drawdown, uptime and unit economics | Cost/impact grows faster than edge or operational risk rises | Only stepwise increases with preserved net expectancy and risk limits |

No stage is promoted because results “look good.” A failure leaves the product
at its prior safe stage and cannot weaken a registered requirement.

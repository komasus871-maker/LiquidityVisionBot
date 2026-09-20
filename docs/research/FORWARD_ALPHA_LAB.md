# Forward Microstructure Alpha Lab

Status: **OPERATIONAL SHADOW-ONLY INFRASTRUCTURE; FORWARD EVIDENCE PENDING**
Program: `forward-microstructure-alpha-v1`
Production behavior: unchanged
Real-order authority: none

## Operational result

The lab collects public BTCUSDT, ETHUSDT, and SOLUSDT perpetual-market events from Binance, OKX, and BingX. It preserves venue provenance and both exchange and local receive clocks in an append-only SQLite ledger. Raw events, feature snapshots, Shadow decisions, future labels, and recovery checkpoints reject update/delete operations at the database layer.

The credential-free certification run connected once to every venue and wrote 2,090 events, 164 feature identities, 93 Shadow decision identities, and 164 elapsed-horizon labels. It reported zero book gaps and no final connector error. Its receive-order replay processed 2,088 first-seen events and reproduced every feature identity and all 93 decision identities exactly. The two excluded records were explicitly retained duplicates.

After storage hardening, a second 10-second certification of event schema v2 wrote 1,023 raw events with only nine checkpoints, zero book gaps, and no connector errors. Replay consumed all 1,022 first-seen events and exactly reproduced 73 feature and 40 Shadow-decision identities. This run verifies lossless compressed raw payloads and checkpoint throttling; it is also excluded from candidate evidence.

The earlier `certification.sqlite3` probe is a quarantined engineering artifact: it correctly failed closed on OKX checksum zero before the current venue deprecation was incorporated. It is not forward candidate evidence. `certification-v2.sqlite3` and its replay are connectivity/reproducibility certification only and are also excluded from candidate evidence by the preregistration.

The first persistent startup retained three venue-wide OKX liquidation messages outside BTC/ETH/SOL before the subscription scope was observed. Because the raw ledger is immutable they remain auditable, but they had no corresponding book and could not produce a decision. The collector now filters the venue-wide channel before persistence; candidate evaluation remains restricted to the preregistered universe.

## Feeds and honest limits

| Venue | Trades | Book | Liquidations | Derivatives context |
|---|---|---|---|---|
| Binance | aggregate trades with buyer-maker aggressor semantics | REST snapshot + incremental 100ms depth | public force orders | WebSocket mark/index/funding; REST OI |
| OKX | taker-side public trades | incremental depth with strict `seqId/prevSeqId` | public liquidation-orders | WebSocket mark/index/OI/funding |
| BingX | buyer-maker aggressor semantics | bounded top-20 snapshots only | unavailable | WebSocket/REST mark/index/funding; REST OI |

OKX's JSON-book checksum has been fixed to zero since 2026-06-23, so it is not treated as an integrity signal. BingX never claims delta continuity or a liquidation feed. A candidate depending on missing, stale, or invalid state emits no decision.

## Runtime and data

Start one persistent hidden collector from the repository root:

```powershell
pwsh -File tools/start_forward_microstructure_collector.ps1 -Python "C:\Users\komas\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
```

The durable mixed-version ledger is `data/forward_microstructure/forward-events.sqlite3`; new raw payloads are losslessly zlib-compressed. Runtime PID and logs are under `data/forward_microstructure/`. Inspect counts, feed freshness, last book sequence state, gap/out-of-order/duplicate totals, and observed uptime without touching candidate outcomes:

```powershell
python tools/forward_microstructure_status.py
```

Reproduce feature and Shadow decision identities into a new database:

```powershell
python tools/replay_forward_microstructure.py --output data/forward_microstructure/replay-YYYYMMDD.sqlite3
```

Replay refuses to overwrite an existing output. Network reconnects use bounded exponential backoff. Any Binance/OKX sequence break invalidates the book and requests a fresh snapshot; feature/candidate evaluation cannot use the invalid interval. On a local process restart, every unfinished outcome horizon is explicitly closed as `INVALID / PROCESS_RESTART_GAP`; the collector never invents the missing path or silently carries a pending label across the gap.

## Research contract

Exactly five families with two frozen variants each are registered in `research_artifacts/forward_microstructure/experiment-registry.json`: flow/book momentum, absorption reversal, liquidation exhaustion, liquidity-vacuum breakout, and cross-venue lead/lag. They are entry hypotheses, not profitability claims.

Future labels remain separate from feature snapshots. At every predeclared horizon, a hypothetical position is closed at the observable opposite touch plus adverse latency and both taker fees. The 5-minute result is primary; the remaining horizons describe edge half-life. LOW/NORMAL/STRESS latency is 50/250/1,000ms. No threshold, horizon, cost, or state definition may be changed without a new candidate identity and fresh evidence boundary.

The first eligibility review requires all frozen promotion gates, including 30 calendar days, 500 decisions, 250 independent clusters, regime coverage, positive robust expectancy/PF, bounded drawdown, cost/latency robustness, at least two positive venues, and exact replay. Until then the only valid result is `FORWARD_EVIDENCE_PENDING`.

## Verification

- Forward-focused deterministic suite: **22 passed**.
- Accumulated Phase 1A through forward-lab regression suite: **120 passed**.
- Full suite: **599 passed / 6 PRE_EXISTING failures / 0 NEW regressions**.
- No private credentials, order API, LIVE/PAPER/copy adapter, or production default is imported by the forward subsystem.

No historical candidate was retuned, no protected historical split was opened, and no profitability result was inferred from the short engineering certification.

# Production Scanner Data Plane

The Liquidity Vision Scanner is descriptive market intelligence. It has no
trade-decision or execution authority. Pump/dump is a category within this
single Scanner product, not a second scanner runtime.

## Ownership and persisted state

| Stage | Owner | Input | Output / durable state | Freshness and failure evidence |
|---|---|---|---|---|
| Venue feeds | `liquidityvision-forward-worker` | Binance, OKX and BingX public derivatives feeds | Bounded `forward_market_state`; raw evidence remains in the forward archive | `forward_worker_health.venues_json`, with channel ages, symbol coverage, reconnects and error reason |
| Universe discovery | `liquidityvision-operational-worker` / `PumpDumpMonitor` | Binance USD-M exchange info and 24-hour tickers | Deterministic liquid USDT perpetual tuple | Scanner `runtime_state.details_json`: candidates, eligible count, target and completion timestamp |
| Broad radar | Same operational worker | One 241 x 1-minute REST backfill per selected symbol | In-memory immutable `SymbolSnapshot` values for the current cycle | Fetched/failed/baseline-ready counts and failed-symbol reasons in `runtime_state` |
| Adaptive baselines | Same operational worker | The 241-minute provider backfill | Robust z-scores, percentiles, volume and volatility baselines in each frozen event snapshot | `baseline_ready_symbols`; no multi-hour in-process warmup is required after a successful backfill |
| Shortlist | Same operational worker | Broad snapshots and frozen Scanner defaults | Bounded set of anomaly symbols | `shortlisted_symbols` and shortlist timestamp |
| Deep enrichment | Same operational worker | Bounded `forward_market_state` for shortlisted symbols only | Enriched decision-time snapshot | `deep_enrichment_symbols`; unavailable enrichment does not stop broad radar |
| Episode engine | Same operational worker | Genuine detector candidates | Global rows (`telegram_id=0`) in `scanner_episode_state` and `market_anomaly_events` | Active/new/escalation counts, engine timestamp and last error |
| Causal outcomes | Same operational worker | Later broad-radar observations | `scanner_outcome_labels` at 1/3/5/15/30/60/240 minutes | Pending, labeled and sample-ready cohort counters |
| Worker heartbeat | Same operational worker | Child runtime checkpoints | `operational_worker_health` | Independent operational-worker heartbeat and RSS |
| Telegram Scanner | Web service, read-only | Global scanner state plus per-user notification settings | Messages and callbacks only | Uses the same `ScannerRepository.home_stats` as Terminal |
| Terminal Scanner/System | Web service, read-only | Same global scanner state | Authenticated JSON/HTML only | Scanner status card and distributed health detail |

## Health semantics

- `RUNNING`: operational heartbeat, scanner start and successful radar cycle are
  current; the current universe is baseline-ready.
- `WARMING`: the first cycle is running or some symbols do not yet have a valid
  60-minute minimum history.
- `DEGRADED`: the last successful radar is two or more configured cycles old,
  or the latest cycle recorded an error.
- `FAILED`: the operational or scanner heartbeat is unavailable/stale for five
  cycles, or Scanner is disabled in its owning worker.

The Home screen reports scanner heartbeat, broad-radar completion and episode
engine completion separately. Forward-collector heartbeat is shown separately
and is never mislabeled as Scanner freshness.

Venue `HEALTHY` requires fresh trade, ticker and book observations across the
configured symbols. Open interest, funding and liquidation channels are shown
individually but a stale optional channel does not by itself downgrade an
otherwise complete venue. A successful reconnect or new event clears a
superseded transport error.

## Baseline and restart semantics

Each broad cycle requests 241 closed/current one-minute bars. A valid response
therefore reconstructs the required 60-minute minimum plus the longer adaptive
distribution immediately. The durable event snapshot contains the computed
decision-time baselines. Restart recovery does not require persisting a mutable
in-process rolling window and does not synthesize observations.

## Capacity policy

`tools.benchmark_scanner_radar` measures deterministic local CPU time and Python
peak allocations for 40, 75, 100, 150 and 200 symbols. It deliberately does not
claim provider/network certification. Production remains capped at 40 until
deployed cycle duration, RSS, provider responses and PostgreSQL writes prove a
larger universe stays comfortably below the 60-second cadence on the existing
0.5 CPU / 512 MB operational-worker plan.

## Safety

The Scanner stores `classification=MARKET_ALERT`, `economic_authority=0`, and
never imports an order dispatcher. LIVE flags and frozen research candidate
identities are outside this data plane.

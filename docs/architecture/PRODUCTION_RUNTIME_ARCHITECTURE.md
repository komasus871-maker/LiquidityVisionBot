# Production runtime architecture

## Chosen topology

Liquidity Vision uses topology B: one interactive web service, one operational
product worker, and one forward-evidence worker. The processes share PostgreSQL;
only the forward worker has a 50 GB bounded spool disk. Sealed immutable evidence
is archived through an S3-compatible interface rather than retained as Render
block storage for the full research window.

| Runtime | Command | Owns | Must not own |
|---|---|---|---|
| `LiquidityVisionBot-1` | `python bot.py` | Telegram webhook, HTTP health/API, authenticated Terminal, interactive commands | continuous collectors, maintenance, raw evidence, LIVE execution, schema DDL |
| `liquidityvision-operational-worker` | `python -m tools.run_operational_worker` | signal/watch/observation advancement, PAPER copy/lifecycle, anomaly alerts, research projections, bounded retention | webhook/polling, raw forward feeds, LIVE execution |
| `liquidityvision-forward-worker` | `python -m tools.run_forward_microstructure_collector` | Binance/OKX/BingX public feeds, reconstructable books, flow/derivatives features, sealed raw partitions, verified object archive, frozen Shadow evaluations | Telegram, product decisions, execution |
| Web pre-deploy migration | `python -m tools.run_product_migrations` | advisory-lock-protected schema DDL, historical execution migration and trade-memory backfill | service runtime work |

Topology A (combined operational and forward worker) saves one 512 MB service,
but couples raw-evidence durability and Telegram product progression to one
event loop and one failure domain. The forward collector maintains websocket
books and disk segments while the operational worker runs dataframe analysis,
REST radar requests, Telegram delivery, and PAPER lifecycle cycles. Combining
them creates correlated allocation peaks and makes a product-worker restart an
evidence gap. That reliability and evidence-continuity cost outweighs the
additional small worker.

Local Windows startup measurements (working-set RSS after imports and worker
construction) were approximately 189 MB web, 183 MB operational, 59 MB forward,
and 184 MB for a combined operational+forward import. These are startup values,
not steady-state or peaks. Native allocator behavior and live exchange traffic
must be measured on Render. The web and operational processes retain 512 MB
plans with webhook concurrency bounded to 20. The forward worker uses `1c-2g`:
its exact frozen five-minute flow features retain time-bounded event-level
trades, whose count can be very high even though their time horizon is bounded.
A count cap would change research semantics, so memory isolation is the correct
tradeoff.

## Singleton and recovery contracts

The operational process holds `operational-product-production-v1`; the forward
process holds `forward-microstructure-production-v1`. Both leases are renewed
in PostgreSQL and loss is fatal so Render can restart cleanly. Child operational
jobs retain their existing idempotency and per-job leases. PAPER execution uses
durable queue claims; forward collection records restart gaps and resumes from
durable checkpoints. Neither worker has LIVE authority.

Schema DDL has one production authority: the web service's Render
`preDeployCommand`. `run_product_migrations` holds a stable PostgreSQL session
advisory lock, retries bounded deadlock/serialization failures with jitter, and
publishes the committed schema-version marker only through the transactional
`create_tables` path. Web, operational and forward runtime commands perform
read-only schema/version checks and wait for the migration for a bounded period;
they never issue startup DDL. PostgreSQL releases the session lock if the
migration process crashes.

## Maintenance classification

| Task | Classification | Owner |
|---|---|---|
| schema creation/additive migration | serialized pre-deploy prerequisite | web pre-deploy migration command only |
| historical execution migration | idempotent pre-deploy migration | web pre-deploy migration command |
| trade-memory backfill | bounded pre-deploy backfill | web pre-deploy migration command |
| retention | periodic maintenance | operational worker, six-hour default |
| forward raw compaction/manifests | continuous forward durability | forward worker |
| sealed partition upload/verification/local eviction | continuous forward durability | forward worker |
| remote evidence retention release | explicit operator/research authorization only | no automatic owner |

## Forward evidence durability

Five-minute UTC buckets are independently framed, compressed, fsynced, sealed,
checksummed and manifested. The worker uploads both the `.fwdz` object and its
manifest, verifies remote size and SHA-256 metadata, records `REMOTE_VERIFIED`
in a local sidecar and compact PostgreSQL registry, and only then may evict the
local data object. A retry uses the same immutable key: an equal checksum is an
idempotent success and a different checksum is an integrity incident.

The local SQLite database is a bounded, rebuildable cache for feature snapshots,
Shadow decisions and labels. Older resolved derived rows are compacted only when
the corresponding canonical raw time range is remotely verified. Recent rows,
unresolved labels, latest checkpoints and all gap records remain local. Remote
replay selects registry rows, downloads one five-minute bucket at a time, checks
size/SHA-256, merges original ingest order, and removes only the temporary cache.

## Product control inventory

`services.functionality_audit.full_functionality_matrix()` is the executable
full inventory. It covers every normalized slash command and callback in
`FUNCTION_REGISTRY`, all 13 reply-keyboard controls, and all 12 Terminal pages.
Each record includes the entry point, concrete handler, imported service layer,
state owner, background dependency, database tables, expected output, failure
mode, fallback, runtime class, runtime owner, permission and economic authority.

The legacy catch-all inline menu contained Russian placeholder screens for
callback values that no current keyboard emits. Those placeholders were
classified as obsolete/duplicate and replaced with an explicit expired-action
fallback. No current control or route was removed.

## Scanner architecture

The operational scanner is a two-stage, descriptive MARKET ALERT system:

1. Broad radar selects a bounded liquid USDT-perpetual universe from one bulk
   ticker request and uses capped one-minute candle requests. It computes
   multi-window returns, velocity, acceleration, realized volatility,
   relative volume, robust median/MAD z-scores, historical percentiles,
   range/VWAP location, and BTC/ETH/market-relative movement.
2. Only symbols that pass an adaptive anomaly gate are promoted into existing
   bounded forward current-state enrichment for CVD, taker imbalance, OI,
   funding, basis, liquidations, book imbalance, spread and cross-venue state.

Episodes persist phase, previous phase, market-state interpretation, peak
severity, data quality, escalation time and bounded event snapshots. Alerts
are emitted only for new information: a new episode, phase/state transition,
severity escalation, material extension, or cooldown expiry. Stale, invalid,
low-turnover and missing-volume inputs cannot generate high-confidence alerts.
Scanner output is always `MARKET_ALERT` with `economic_authority=false`.

## Terminal

The Mini App loads Telegram's official WebApp JavaScript, validates signed and
unexpired `initData` server-side, maps the Telegram user ID to scoped queries,
and exposes Overview, Markets, Scanner, Signals, Order Flow, Derivatives,
Paper, Portfolio, Risk, Alerts, Shadow, and System. It has no order controls,
raw-ledger access, secrets, or sealed outcome metrics. Direct browser access
shows a safe instruction to reopen through Telegram; API access without valid
`initData` returns HTTP 403.

## Safety invariants

`LIVE_EXECUTION_ENABLED`, `LIVE_DISPATCHER_ENABLED`,
`ALLOW_USER_LIVE_CONNECTIONS`, and `BINGX_PRODUCTION_ADAPTER_ALLOWED` are false
for all three services. Scanner alerts, research, AI, and the Terminal cannot
call execution adapters or bypass `DecisionQualityEngine`.

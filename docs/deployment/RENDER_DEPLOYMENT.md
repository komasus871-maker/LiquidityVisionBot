# Render deployment

## Production shape

The Blueprint defines three independently restartable services:

1. `liquidityvisionbot-1` is the Telegram webhook web service. It exposes
   `/health`, accepts Telegram updates, serves analysis, alerts, PAPER and the
   authenticated read-only Mini App terminal, and stores shared product state
   in PostgreSQL. It does not run continuous product or research loops.
2. `liquidityvision-operational-worker` owns lease-protected signal, watchlist,
   observation, PAPER lifecycle, alert, anomaly-radar, research projection and
   retention cycles. It can send notifications but owns no webhook or polling.
3. `liquidityvision-forward-worker` is the single continuous public-data
   collector. It writes compressed append-only raw partitions to a bounded
   `/var/data/forward_microstructure` spool, moves sealed/verified evidence to
   S3-compatible object storage, and keeps a bounded local derived cache. It
   publishes compact partition identity, bounded current-market state, gaps and
   health to PostgreSQL.

The Telegram service never reads the worker disk and never scans raw events.
The forward worker has no Telegram token, exchange credentials, order adapter or
execution authority. A PostgreSQL lease prevents two production collectors
from writing the same experiment.

Start commands are `python bot.py`, `python -m tools.run_operational_worker`,
and `python -m tools.run_forward_microstructure_collector`.

## Required configuration

Set these secrets on the existing Telegram web service:

- `BOT_TOKEN`: the existing BotFather token (do not create a new bot).
- `DATABASE_URL`: the internal URL of the existing Render PostgreSQL database.
- `MONITOR_CRON_SECRET`: a long random secret.
- `WEBHOOK_BASE_URL`: the existing public service URL if it differs from the
  Blueprint value.
- Role-specific Telegram admin ID variables already present in `render.yaml`.

Set `DATABASE_URL` on both workers to the **same** internal PostgreSQL URL.
Set `BOT_TOKEN` on the operational worker for outbound Telegram notifications.
No private exchange API keys belong on the forward worker.

Set these forward-worker object-storage secrets. Use a least-privilege key
restricted to the single evidence bucket/prefix (object read/write/list/head;
no delete or account administration). Any later authorized retention deletion
should use a separate short-lived maintenance credential:

- `FORWARD_OBJECT_ENDPOINT_URL`: provider S3 endpoint (R2 or B2); use the AWS
  endpoint when AWS S3 is selected.
- `FORWARD_OBJECT_REGION`: provider region (`auto` for R2 where supported).
- `FORWARD_OBJECT_BUCKET`: dedicated immutable-evidence bucket.
- `FORWARD_OBJECT_ACCESS_KEY_ID` and `FORWARD_OBJECT_SECRET_ACCESS_KEY`.

The non-secret prefix is `forward-evidence/schema-v2`. Never place any of these
credentials in Git, the disk, manifests, health output, or PostgreSQL.

The Blueprint locks `LIVE_EXECUTION_ENABLED=false`,
`LIVE_DISPATCHER_ENABLED=false`, `ALLOW_USER_LIVE_CONNECTIONS=false`, and
`BINGX_PRODUCTION_ADAPTER_ALLOWED=false` on all three services. The web and
operational services use `PAPER`; the forward worker uses `SHADOW`. Do not
override these values.

`TELEGRAM_BOT_TOKEN` is accepted as an explicit alias for local or future
configuration, but if both token variables are present they must match.

The operational worker fixes the scanner budget at:

```text
PUMP_SCANNER_ENABLED=true
PUMP_SCANNER_INTERVAL_SECONDS=60
PUMP_SCANNER_UNIVERSE_LIMIT=40
PUMP_SCANNER_CONCURRENCY=5
```

Scanner settings and episodes are persisted in PostgreSQL. No scanner event is
a trade signal or input to production execution.

## Forward evidence migration boundary

The preserved local ledger ends at exactly
`2026-09-20T11:30:50.504000Z` (`205,515` raw rows). The Blueprint sets that
timestamp as `FORWARD_PREVIOUS_EVIDENCE_END_UTC`. On the first hosted start,
the worker records a non-replayable `LOCAL_TO_RENDER_MIGRATION_GAP` for every
venue/symbol from that timestamp to the hosted start. It never synthesizes
missing events and never treats the gap as feature-eligible evidence.

Keep the local SQLite artifact unchanged. It is a prior evidence segment, not
a database to run concurrently. Stop all local forward collectors before the
Render worker is enabled.

The ten frozen candidate identities, feature schema, parameters, label
horizons, fill costs and first-evidence rules remain source-verified at worker
startup. Any identity mismatch blocks startup.

## Persistent storage

Only `/var/data` survives worker restarts. The configured 50 GB disk is a spool,
not the 30-day archive. It retains a 5 GiB verified local cache and a 5 GiB
fail-closed free-space reserve. During an archive outage both raw partitions and
the temporarily uncompacted derived cache grow at the measured combined rate of
13.8406 GB/day; after cache occupancy this gives about 68.7 hours of outage
tolerance. The collector continues retrying with bounded exponential backoff,
reports `DEGRADED`, and fails closed before the reserve rather than discarding
evidence. Render disks can still be expanded later.

Partitions use five-minute UTC buckets under date/hour, venue, symbol and event
type. Every sealed object records receive/exchange bounds, count, size, SHA-256,
event coverage, collector version and frozen program identity. Frame CRCs detect
partial writes. A local copy is evictable only after data and manifest objects
both pass remote verification. The PostgreSQL registry contains identity,
location, checksum, state and retention metadata—never raw payloads.

## Deployment order

1. Confirm no local collector process is running.
2. Commit and push the deployment changes.
3. In Render, create or sync a Blueprint from this repository's `render.yaml`.
   Reuse the existing `LiquidityVisionBot-1` service; do not create a second
   Telegram web service with the same token.
4. Supply the existing web-service secrets when prompted.
5. Set the same internal `DATABASE_URL` on all three services and the existing
   `BOT_TOKEN` on web plus the operational worker.
6. Confirm the web service's `preDeployCommand` is
   `python -m tools.run_product_migrations`. It is the only schema-DDL authority. All three
   runtime commands use `SCHEMA_STARTUP_MODE=wait` and may start concurrently;
   they continue only after the committed schema marker is ready. A manual
   one-off invocation remains safe and idempotent but is not required per deploy.
7. Create the dedicated object bucket/key, set the five object-storage secrets,
   and verify the forward worker has the 50 GB `forward-evidence` disk mounted at
   `/var/data` with exactly one instance.
8. Sync the Blueprint. Deployment ordering is not a correctness dependency:
   the advisory lock serializes migration contenders and runtime processes wait
   with a bounded timeout. Do not start a local collector after the hosted
   worker acquires its lease.

## Verification

- Confirm migration logs contain `MIGRATION_LOCK_ACQUIRED`,
  `MIGRATION_STARTED`, `MIGRATION_COMPLETE`, `MIGRATION_LOCK_RELEASED`, and
  `SCHEMA_READY`; each runtime must then log its own `SCHEMA_READY` event.
- Open `https://liquidityvisionbot-1.onrender.com/health`; expect the fast web
  readiness response with HTTP 200. Distributed and PostgreSQL state remains on
  `/terminal_health` and Terminal System rather than Render's promotion probe.
- In Telegram, run `/start`, `/market_now`, `/orderflow BTCUSDT`, `/pump_scan`,
  `/scanner_settings`, `/deep_analyze BTC 1h`, `/terminal`, `/shadow_status`,
  `/terminal_health`, `/analyze BTC 1h`, `/copy`, and `/positions`.
- Open the Mini App only from Telegram. Its API must reject absent, tampered,
  or expired Telegram `initData` with HTTP 403.
- `/shadow_status` must show a fresh heartbeat and exactly ten frozen Shadow
  candidates without WR, PF, expectancy or PnL.
- Operational logs must show one master lease and advancing product cycles.
- Forward-worker logs must show one lease owner and continuing Binance/OKX/BingX
  events. A second worker must exit with `lease is already held`.
- Confirm the spool contains `forward-metadata.sqlite3`, active partitions, and
  local manifests/remote sidecars. Confirm sealed objects appear under
  `forward-evidence/schema-v2/<venue>/<symbol>/YYYY/MM/DD/HH/`.
- `/terminal_health` and Terminal System must show object status, pending bytes,
  remaining spool hours, last upload, verified bytes, and zero integrity faults.
- Confirm every LIVE flag remains false in the Render effective environment.

## Restart and failure behavior

On a normal deploy Render sends `SIGTERM`; the forward worker flushes, seals and
attempts to archive active segments within the configured shutdown window. On
restart it scans sealed/unverified manifests and resumes uploads without changing
keys or inventing events. An archive outage retains local sources and backs off;
a checksum/key collision is `CRITICAL` and blocks eviction. It also invalidates
unfinished label horizons, records the downtime gap, resynchronizes books, and
continues with new public events. Telegram remains independently available.

## Rollback

Roll back the web service and workers independently to the previous Git commit.
Never delete or replace the forward-worker disk during rollback. If the worker version
is rolled back, leave it stopped unless that version understands the existing
partition schema. The application can remain online with the worker stopped.
Keep LIVE disabled throughout rollback.

## Commit and push

Review the diff before staging. This working tree intentionally contains the
preserved reconstruction and frozen forward-lab implementation on which the
deployment depends, so commit the verified tree as one coherent state:

```powershell
git diff --check
git status --short
git add -A
git diff --cached --check
git commit -m "Deploy Telegram app and forward lab as separate Render services"
git push origin HEAD
```

Do not commit `.env`, database files, raw partitions, credentials, or generated
research outcomes.

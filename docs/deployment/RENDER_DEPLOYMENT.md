# Render deployment

## Production shape

The Blueprint defines two independently restartable services:

1. `liquidityvisionbot-1` is the Telegram webhook web service. It exposes
   `/health`, accepts Telegram updates, serves analysis, alerts, PAPER and the
   authenticated read-only Mini App terminal, and stores shared product state
   in PostgreSQL. Its resource-bounded broad scanner uses public bulk tickers
   plus capped one-minute candle requests; it never subscribes broad L2.
2. `liquidityvision-forward-worker` is the single continuous public-data
   collector. It writes compressed append-only raw partitions and its local
   research metadata database under `/var/data/forward_microstructure` on its
   attached persistent disk. It publishes only bounded current-market state,
   experiment identity, gaps and a heartbeat to the same PostgreSQL database.

The Telegram service never reads the worker disk and never scans raw events.
The worker has no Telegram token, exchange credentials, order adapter or
execution authority. A PostgreSQL lease prevents two production collectors
from writing the same experiment.

Start commands are `python bot.py` for the web service and
`python -m tools.run_forward_microstructure_collector` for the worker.

## Required configuration

Set these secrets on the existing Telegram web service:

- `BOT_TOKEN`: the existing BotFather token (do not create a new bot).
- `DATABASE_URL`: the internal URL of the existing Render PostgreSQL database.
- `MONITOR_CRON_SECRET`: a long random secret.
- `WEBHOOK_BASE_URL`: the existing public service URL if it differs from the
  Blueprint value.
- Role-specific Telegram admin ID variables already present in `render.yaml`.

Set `DATABASE_URL` on `liquidityvision-forward-worker` to the **same** internal
PostgreSQL URL. No private exchange API keys belong on the worker.

The Blueprint locks `LIVE_EXECUTION_ENABLED=false`,
`LIVE_DISPATCHER_ENABLED=false`, `ALLOW_USER_LIVE_CONNECTIONS=false`, and
`BINGX_PRODUCTION_ADAPTER_ALLOWED=false`. The web service uses `PAPER`; the
forward worker uses `SHADOW`. Do not override these values.

`TELEGRAM_BOT_TOKEN` is accepted as an explicit alias for local or future
configuration, but if both token variables are present they must match.

The web service also fixes the scanner budget at:

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

Only `/var/data` survives worker restarts. The configured 100 GB disk is a
conservative initial allocation, not a guaranteed 30-day capacity. Check the
worker's `storage.free_bytes` heartbeat and Render disk metrics after the first
24 hours, calculate the measured daily growth rate, and increase the disk well
before the 10 GiB safety floor. Render disks can be increased but not reduced.
If free space crosses the safety floor, the collector fails closed instead of
silently losing evidence.

Each raw partition is split by UTC date, venue, symbol and event type. Every
finalized segment has a manifest containing count, byte size, time bounds and
SHA-256. Frame CRCs make partial/truncated writes detectable. The SQLite file
on disk holds only bounded research metadata, checkpoints, decisions and
outcome labels; shared operational reads use PostgreSQL.

## Deployment order

1. Confirm no local collector process is running.
2. Commit and push the deployment changes.
3. In Render, create or sync a Blueprint from this repository's `render.yaml`.
   Reuse the existing `liquidityvisionbot-1` service; do not create a second
   Telegram web service with the same token.
4. Supply the existing web-service secrets when prompted.
5. Set the same internal `DATABASE_URL` on both services.
6. Verify the worker has the `forward-evidence` disk mounted at `/var/data` and
   exactly one instance.
7. Deploy the worker, then deploy the web service. Do not start a local
   collector after the hosted worker acquires its lease.

## Verification

- Open `https://liquidityvisionbot-1.onrender.com/health`; expect HTTP 200 and
  PostgreSQL persistence.
- In Telegram, run `/start`, `/market_now`, `/orderflow BTCUSDT`, `/pump_scan`,
  `/scanner_settings`, `/deep_analyze BTC 1h`, `/terminal`, `/shadow_status`,
  `/terminal_health`, `/analyze BTC 1h`, `/copy`, and `/positions`.
- Open the Mini App only from Telegram. Its API must reject absent, tampered,
  or expired Telegram `initData` with HTTP 403.
- `/shadow_status` must show a fresh heartbeat and exactly ten frozen Shadow
  candidates without WR, PF, expectancy or PnL.
- Worker logs must show one lease owner and continuing Binance/OKX/BingX
  events. A second worker must exit with `lease is already held`.
- Confirm the persistent disk contains `forward-metadata.sqlite3` and dated
  `raw/.../*.fwdz` segments.
- Confirm every LIVE flag remains false in the Render effective environment.

## Restart and failure behavior

On a normal deploy Render sends `SIGTERM`; the worker flushes and finalizes raw
segments within the configured shutdown window. On restart it invalidates
unfinished label horizons, records the downtime gap, resynchronizes books, and
continues with new public events. Sequence gaps trigger a book resync and remain
visible in checkpoints. Telegram remains available if the worker is down;
`/shadow_status` reports a stale heartbeat.

## Rollback

Roll back the web service and worker independently to the previous Git commit.
Never delete or replace the worker disk during rollback. If the worker version
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

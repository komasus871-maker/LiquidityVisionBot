# Liquidity Vision v4.6 — Persistent Automation

## What changed

- Added PostgreSQL support through `DATABASE_URL`.
- Kept SQLite as a local-development fallback.
- Added the missing `watch_states` table.
- Watchlist monitor now persists every analysis/observation on every cycle, not only after a visible status change.
- Strong watchlist setups are promoted into the signal lifecycle and tracked for TP1/TP2/TP3/STOP.
- Added a protected `/internal/monitor` endpoint for an external scheduler. This is useful on free Render because background loops pause while the service sleeps.
- Fixed graceful shutdown for SignalTracker.
- Database logs now show the active backend (`postgresql` or `sqlite`).

## Required Render settings

```text
BOT_MODE=webhook
BOT_TOKEN=...
WEBHOOK_BASE_URL=https://YOUR-SERVICE.onrender.com
DATABASE_URL=postgresql://...
PGSSLMODE=require
MONITOR_CRON_SECRET=a-long-random-secret
```

## Free Render monitoring

A free Web Service can sleep. While asleep, in-process monitors do not execute. Configure an external scheduler to call every 5 minutes:

```text
https://YOUR-SERVICE.onrender.com/internal/monitor?token=YOUR_SECRET
```

The endpoint runs one Watchlist, Observation, and Signal lifecycle cycle.

## Safety

Do not expose `MONITOR_CRON_SECRET` publicly. The endpoint returns `403` without the correct token.

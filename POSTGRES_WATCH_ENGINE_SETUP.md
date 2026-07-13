# Persistent database and Watch Engine setup

## Required Render environment variables

```env
BOT_MODE=webhook
BOT_TOKEN=...
WEBHOOK_BASE_URL=https://YOUR-SERVICE.onrender.com
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require
PGSSLMODE=require
REQUIRE_PERSISTENT_DB=true
MONITOR_CRON_SECRET=use-a-long-random-secret
```

`REQUIRE_PERSISTENT_DB=true` intentionally stops startup when PostgreSQL is not configured. This prevents Render from silently creating a temporary SQLite database and losing users, Premium, Watchlist, observations, signals and statistics after a redeploy.

## Render commands

```text
Build Command: pip install -r requirements.txt
Start Command: python bot.py
Health Check Path: /health
```

## Free Render monitoring

A sleeping free service cannot execute Python background loops. Configure an external scheduler to call every five minutes:

```text
GET https://YOUR-SERVICE.onrender.com/internal/monitor?token=MONITOR_CRON_SECRET
```

The endpoint runs one lease-protected cycle of:

1. Watch Engine
2. Observation Monitor
3. Signal Tracker

Calling it twice at the same time is safe: database leases prevent duplicate processing during rolling deploys or overlapping scheduler requests.

## Verification

Open:

```text
https://YOUR-SERVICE.onrender.com/health
```

Required fields:

```json
{
  "database_backend": "postgresql",
  "persistent_database": true,
  "database": {"ok": true},
  "workers": []
}
```

After the first monitor cycle, `workers` will contain timestamps and processed/error counters for `watch_engine`, `observation_monitor`, and `signal_tracker`.

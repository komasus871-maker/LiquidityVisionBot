# v4.7.1 Persistent Watch Engine Stability

- Fixed malformed dependency line in `requirements.txt`.
- Added fail-fast PostgreSQL requirement for production.
- Added portable PostgreSQL/SQLite migrations.
- Added persistent Watch states, Watch events, worker diagnostics and distributed leases.
- Watch Engine now refreshes observations every cycle, promotes valid setups, persists errors, and sends only material-change notifications.
- Observation and Signal workers are lease-protected against rolling deploy and cron overlap.
- `/health` now reports database latency, persistence and worker status.
- Removing a Watchlist item also removes its stored Watch state.
- Clean release excludes `.git`, local SQLite databases, caches and nested project copies.

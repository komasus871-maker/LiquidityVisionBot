# v4.5.3 — Webhook Runtime Fix

- Render now runs in Telegram webhook mode instead of long polling.
- Removed the `getUpdates` conflict during rolling deploys.
- Added `/telegram/webhook`, `/health`, and `/healthz` routes.
- Telegram updates are acknowledged immediately and processed in tracked tasks.
- Added duplicate update protection and webhook secret validation.
- Added bounded analysis concurrency and Scanner cache/lock.
- CPU-heavy pandas analysis runs outside the asyncio event loop.
- WatchEngine now starts with the bot.
- SQLite uses WAL, busy timeout, and foreign keys.
- Fixed `/analyze` commands while the ticker-input FSM is active.
- Removed unused legacy modules with broken imports.

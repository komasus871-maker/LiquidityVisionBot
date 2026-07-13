# Liquidity Vision v4.5 — Autonomous Watch Engine

- Personal OKX futures watchlist is re-analyzed automatically.
- Default interval: 5 minutes (`WATCHLIST_CHECK_INTERVAL`).
- Notifications are sent only for material changes: status/direction changes, major score/readiness shifts, BOS/CHOCH, or preferred-zone entry.
- The first scan initializes state silently to avoid spam.
- Meaningful executable changes are promoted into the normal signal lifecycle and Journal.
- Watchlist UI shows current status and supports removal.

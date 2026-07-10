# Liquidity Vision V3 — Autonomous Lifecycle Sprint 1

Implemented:
- full lifecycle: WATCHING → TRIGGERED → ACTIVE → TP1/TP2/TP3 or STOP;
- INVALIDATED and EXPIRED states;
- preferred-zone monitoring and directional candle activation;
- automatic Telegram lifecycle notifications;
- per-user signal ownership and personal journal statistics;
- duplicate signal refresh instead of duplicate rows;
- signal event log;
- Telegram Stars Premium invoice flow;
- premium subscription storage and expiry;
- richer Journal PRO and Profile screens.

Environment options:
- `PREMIUM_STARS` (default `199`)
- `PREMIUM_DAYS` (default `30`)
- `CRYPTO_PAYMENT_TEXT` (manual crypto payment instructions)

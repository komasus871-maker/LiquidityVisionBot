# LiquidityVisionBot v10.3.0

Release: **Operational Intelligence and Fail-Closed LIVE Foundation**

This release is production deployable while LIVE remains disabled. It does not
authorize real-money execution, make authenticated trading requests during
validation, call a paid AI provider, or change PAPER risk policy automatically.

## Delivery status

| Area | Status | Operational meaning |
|---|---|---|
| Collector seed, independent depth/funding/OI, durable health | IMPLEMENTED / TESTED | Production still needs the first real public samples verified after deployment. |
| Bounded derivative and microstructure history | IMPLEMENTED / TESTED | Percentiles and trends remain unavailable until real sample gates are met. |
| Quality V4, Readiness V3, Priority V3, Ranking V5, Fusion V2 | IMPLEMENTED / TESTED | Research scores, never probabilities or execution authority. |
| Help V3, search, classification, menu, five languages | IMPLEMENTED / TESTED | English fallback remains intentional for low-value legacy strings. |
| AI current/history and bounded compiler reuse | IMPLEMENTED / TESTED | Provider remains `disabled`; one deliberate post-deploy certification is optional. |
| Per-user credentials/read paths/LIVE lifecycle | IMPLEMENTED / TESTED | Authenticated production behavior awaits controlled external verification. |
| LIVE intent, idempotency, fills, recovery, risk and kill switches | IMPLEMENTED / TESTED | Globally and per-exchange disabled by default. |
| Reconciliation mismatch detection | IMPLEMENTED / TESTED | Orders, fill aggregates and durable local position quantities are checked against exchange truth; mismatches block new exposure. |
| Periodic reconciliation and LIVE safety alert delivery | IMPLEMENTED / TESTED | Bounded lease-protected worker selects only explicitly enabled accounts; critical alerts bypass cosmetic preferences. |
| User analytics export | IMPLEMENTED / TESTED | Entitled, daily-limited JSON/CSV exports contain only the requesting user's aggregate PAPER analytics. |
| Real-money order execution | DISABLED_BY_DEFAULT | Not production-verified and must not be enabled in this release rollout. |

## Production collector root cause and repair

The worker started correctly and acquired its lease, but `_symbols()` derived its
entire universe from signals, candidates, observations and watchlists. A fresh
or quiet production database therefore produced zero symbols, zero requests and
a superficially successful cycle forever. `MICROSTRUCTURE_SYMBOLS` now supplies
a bounded seed (default `BTCUSDT`) and is unioned with the dynamic universe.

The BingX public adapter now exposes independent funding and open-interest
operations. The worker attempts depth, funding and OI separately, persists every
valid bounded result, records source-specific failures and retains depth if a
derivative endpoint fails. `/system_health`, `/orderbook` and `/data_health`
surface durable worker/source state rather than hiding the failure behind a
generic waiting message.

Collector states are `DISABLED_BY_CONFIGURATION`, `NOT_STARTED`,
`WAITING_FOR_FIRST_SAMPLE`, `HEALTHY`, `DEGRADED`, `STALE` and `FAILED`.
Telemetry includes configuration/effective state, start/heartbeat, lease,
owner, universe, cycle timestamps/duration, last success by source, persistence,
safe error code, failure streak, samples and attempted/succeeded symbols.

## Intelligence semantics

- `GLOBAL_SOURCE_HEALTH` describes present source collection.
- `DECISION_SNAPSHOT_AVAILABILITY` describes immutable facts captured for one
  decision. Current/future data is never used to reconstruct a historical gap.
- Funding percentile requires at least 20 observations. Delta, percentage
  change, acceleration and price/OI states use only observations at or before
  the requested cutoff.
- Signal Quality V4 normalizes across available independent evidence families;
  an unavailable family is `null`, not a neutral score and not zero.
- Entry Readiness V3 separately exposes Setup Quality, readiness and Data
  Confidence, with `READY`, `WAIT_STRUCTURE`, `WAIT_CONFIRMATION`,
  `WAIT_PULLBACK`, `CHASING`, `INVALID` and `INSUFFICIENT_DATA`.
- Scanner Priority V3 is an absolute evidence score, not the EV rank copied or
  percentile-normalized. Structure conflicts and invalid data are capped.
- Strategy Fusion reports `PRIMARY`, `SECONDARY`, `HYBRID`, `TIE` or
  `LOW_CLASSIFICATION_CONFIDENCE`. Ties have no invented primary strategy.
- Liquidity SMC in Strategy Lab is explicitly the production-eligibility
  baseline. Pairwise overlap reports distinguish small-cohort coincidence from
  shared implementation.

## LIVE lifecycle and authority

Per-user identity is Telegram user + exchange + connection. The lifecycle is:

`NOT_CONNECTED → READ_ONLY_CONNECTED → PREFLIGHT_READY → LIVE_CERTIFIED → LIVE_ENABLED`

with terminal/safety states `SUSPENDED`, `REVOKED`, `ERROR` and `KILLED`.
Credential connection, Premium, two-step confirmation, synchronization and
certification never perform the `LIVE_ENABLED` transition. Enablement requires
the private-chat token `ENABLE_LIVE_<account_id>` plus all server-side gates.

Credentials use `cryptography.fernet.Fernet` authenticated encryption. Rows
contain ciphertext, a non-secret SHA-256 key fingerprint prefix and key version.
`EXCHANGE_CREDENTIALS_MASTER_KEYS_JSON` can retain old versions while
`EXCHANGE_CREDENTIALS_KEY_VERSION` selects the current encryption key. Rotation
forces LIVE off and requires confirmation/certification again. Access, create,
rotation and revocation are audited without credential material. Recommended
exchange permissions are **READ + TRADE, never WITHDRAWAL**.

Every active risk profile must define position, order, portfolio and symbol
exposure limits; realized and total daily loss; modeled slippage; cooldown;
symbol/timeframe/strategy/direction allowlists; blocklist; and leverage. Missing
or unresolved context fails closed. Environment-defined global ceilings cannot
be overridden by the user or a plan.

The execution boundary accepts only `DETERMINISTIC_APPROVED_PLAN`. It persists
an immutable intent and checksum before an adapter call, uses persisted client
order identity, records attempts/acknowledgments/fills, aggregates multiple
partial fills and recovers ambiguous submissions through exchange truth.
AI/research services do not import the LIVE coordinator or call `place_order`.

Kill switches exist at GLOBAL, EXCHANGE, USER and CONNECTION scopes and block
new entries. They do not automatically close positions. PAPER `/panic` remains
separate; no ambiguous LIVE panic command exists.

## Required and optional environment

Always required in Render: `BOT_TOKEN`, `DATABASE_URL`, `WEBHOOK_BASE_URL`,
`MONITOR_CRON_SECRET`, `REQUIRE_PERSISTENT_DB=true`, `PGSSLMODE=require` and
`APP_VERSION=10.3.0`.

Collector production rollout: `MICROSTRUCTURE_COLLECTION_ENABLED=true`,
`MICROSTRUCTURE_SYMBOLS=BTCUSDT`.

Safe LIVE-disabled rollout: `LIVE_EXECUTION_ENABLED=false`,
`LIVE_EXCHANGE_BINGX_ENABLED=false`, `ALLOW_USER_LIVE_CONNECTIONS=false`,
`BINGX_PRODUCTION_ADAPTER_ALLOWED=false`.

Periodic exchange-truth reconciliation is implemented as a bounded worker and
may remain enabled with LIVE globally off; it selects only accounts already in
`LIVE_ENABLED`. A mismatch or unavailable reconciliation source suspends that
connection, blocks new entries and sends a critical safety alert without
auto-closing positions. Configure `LIVE_RECONCILIATION_ENABLED=true` and
`LIVE_RECONCILIATION_INTERVAL_SECONDS=120`.

The `LIVE_GLOBAL_MAX_*` variables and `LIVE_GLOBAL_MIN_COOLDOWN_SECONDS` are
non-bypassable server ceilings/floor. `/live_risk ... set` replaces a user's
complete per-connection policy atomically; partial policies remain blocked.

`EXCHANGE_CREDENTIALS_MASTER_KEY` (a generated Fernet key) is required only if
users connect accounts. Use `EXCHANGE_CREDENTIALS_KEY_VERSION=v1`; use the JSON
keyring only during rotation. AI remains `AI_TRADING_MODE=AI_OFF` and
`AI_PROVIDER=disabled`.

## Safe deployment and verification

1. Keep every LIVE flag false and AI disabled.
2. Deploy the additive schema and run migrations twice; the second run must be
   a no-op.
3. Run `/system_health` and verify the collector is started with `BTCUSDT`.
4. After one interval run `/orderbook BTCUSDT`, `/funding BTCUSDT`,
   `/open_interest BTCUSDT` and `/data_health BTCUSDT`. A partial public outage
   must show the exact source degraded while valid sources remain visible.
5. Run `/help`, `/help live`, `/help search orderbook`, `/commands funding`,
   `/language he`, `/scanner`, `/watchlist`, `/usage` and `/admin_health`.
6. For a consented test account only, verify `/connect_exchange ... demo`,
   `/my_exchanges`, `/live_status bingx`, `/live_sync bingx BTCUSDT`,
   `/live_readiness bingx`, `/live_certify bingx` (dry structural path) and
   `/live_reconciliation bingx`. Do not run economic certification in the
   initial rollout.
7. Recheck `/live_status bingx`: LIVE must be OFF.

Future activation requires a separate reviewed change window: provision and
audit a complete risk profile, verify credential permissions exclude
withdrawal, complete read sync and VST certification, run clean reconciliation,
release the global/exchange/deployment adapter gates, then have the user issue
the exact private confirmation. Start with a tightly bounded account and an
operator observing every intent, acknowledgment, fill and reconciliation event.
This document does not authorize that activation.

## Next evidence milestone

Accumulate real bounded depth/funding/OI history, at least 20 funding points,
resolved decision-time Quality/Readiness cohorts, at least 100 resolved
1m/3m/5m after-cost samples, and forward samples meeting each frozen
hypothesis's configured gate. Collect reconciliation-clean demo/VST evidence
across timeouts, restarts, duplicates, partial fills and cancels. Do not change
production intelligence thresholds, copy guardrails or LIVE risk ceilings from
rejection volume alone.

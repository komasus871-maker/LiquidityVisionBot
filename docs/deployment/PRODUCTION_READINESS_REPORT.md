# Production readiness report

This report covers the uncommitted working tree based on production commit
`22145aa`. No commit, push, deploy, real order, frozen-candidate mutation, or
historical-result change was performed.

## 1. Full functionality matrix

`services.functionality_audit.full_functionality_matrix()` is the executable
authority. It currently returns 256 unique traced records: 232 interactive,
21 continuous-product, one periodic-maintenance, one forward-research, and one
one-time-migration record. It includes every normalized command/callback, all
13 reply controls, 13 Terminal pages, 17 scanner modes, 14 alert categories,
and 10 background/maintenance/migration components. Every record has entry
point, handler, service layer, data source, state owner, persistent dependency,
tables, output, failure mode, fallback, class, owner, permissions, and economic
authority.

## 2. Runtime ownership matrix

| Owner | Command | Sole responsibility |
|---|---|---|
| `liquidityvisionbot-1` | `python bot.py` | webhook, HTTP/API, Terminal, interactive commands |
| `liquidityvision-operational-worker` | `python -m tools.run_operational_worker` | product lifecycle, PAPER, alerts, radar, bounded maintenance |
| `liquidityvision-forward-worker` | `python -m tools.run_forward_microstructure_collector` | public forward feeds, raw evidence, bounded current state, frozen Shadow |
| Web pre-deploy migration | `python -m tools.run_product_migrations` | advisory-lock schema DDL, historical migration and memory backfill |

The two workers use independent PostgreSQL singleton leases. The web owns no
continuous collector. The forward worker has no Telegram or execution authority.

## 3. Obsolete behavior removed

The catch-all menu's Russian placeholder callback screens were unreachable from
the current keyboard and duplicated real routes. They were replaced by one
explicit expired-action fallback. No current command, button, callback, or
product capability was removed.

## 4. Telegram status

All 13 reply controls resolve to concrete handlers; registered callbacks are
source-resolved; `/help` and BotCommand ordering remain intact. Failures return
an explicit unavailable/retry response rather than fake data. Live Telegram
delivery still requires post-deploy smoke testing with the real token.

## 5. Terminal root cause

The generated HTML used a normal Python triple-quoted string containing the
JavaScript expression `.join("\\n")`. Python converted that escape into a
literal newline inside a JavaScript string, producing a parse error before the
Telegram bridge or authentication code could execute. The static
`Authenticating…` text therefore remained forever even though the page loaded.

## 6. Terminal fix

The HTML is now a raw Python string, loads the official Telegram bridge with a
bounded timeout, calls `ready()`/`expand()`, exchanges HMAC-validated unexpired
`initData` for a short-lived signed bearer session, and bootstraps only after
authentication succeeds. Auth and page requests have abort timeouts. Every
failure reaches a safe reason code plus Retry and Close/Back; no raw init data,
token, hash, or secret is logged. Thirteen pages read bounded authoritative
state, including a read-only economic dashboard. No order control is client-side.

## 7. Scanner architecture

Stage 1 uses a bounded liquid-USDT-perpetual radar with multi-window returns,
velocity/acceleration, volatility, range/VWAP geometry, relative volume,
median/MAD z-scores, historical percentiles, and BTC/ETH/market-relative
movement. Stage 2 reads expensive forward-state enrichment only for shortlisted
symbols. It never streams full L2 for the broad universe.

## 8. Scanner intelligence

Evidence-driven states include buildup, early anomaly/expansion, momentum and
leveraged expansion, squeezes, liquidation cascade, liquidity vacuum,
cross-venue dislocation, absorption, and exhaustion. Cards expose reasons,
risk flags, freshness, source, abnormality, relative movement, and evidence
quality. All output is descriptive `MARKET_ALERT` with
`economic_authority=false`.

## 9. Scanner current functionality

The visible `⚡ Scanner` label and bare `/scanner` are now explicit home-entry
routes and can no longer be interpreted as internal view names. Scanner Home
shows the canonical 16 product views, actual current episode/cycle statistics,
freshness and normalized venue health. Every ranked row is ownership-scoped and
has an explicit WHY path. The same discovery-mode roster is selectable in the
Terminal Scanner API/UI.

Persistent PostgreSQL episodes track current/previous phase, market state,
peak move/severity, data quality, escalation and end state. Alerts fire on new
episodes, information-changing transitions/escalations/extensions, or cooldown.
Fifteen discovery modes and fourteen per-user alert categories are supported,
along with scope, whitelist/blacklist, windows, severity, cooldown, quiet mode,
and notification enablement. Historical context discloses sample size and never
emits probability. Each admitted event now schedules immutable, causal 1m, 3m,
5m, 15m, 30m, 1h and 4h research labels. Later scanner-cycle observations mature
forward return, MFE/MAE, extrema timing, extension, retracement and explicit
fee/slippage/funding-adjusted outcomes without mutating the decision snapshot.
Cohort attribution is sample-gated at 30 and reports descriptive bootstrap,
temporal, concentration and cost-stress evidence with zero economic authority.

## 10. Signals and watchlists

`SignalTracker`, `ObservationMonitor`, and `WatchEngine` now run exactly once in
the operational worker with durable state, leases/idempotency, bounded cycles,
heartbeat visibility, and restart recovery. The web is presentation/control only.

## 11. PAPER and copy

`CopyExecutionWorker` remains PAPER-only and advances durable queues, plans,
orders, fills, positions, SL/TP/partial-close lifecycle, journal and accounting.
DecisionAuthority and recovery suites pass. No LIVE worker is started.

## 12. Portfolio and risk

Telegram and Terminal use the authoritative execution-portfolio engine and
`paper_execution_positions`; they do not read the obsolete legacy position
surface or invent balances/profitability.

## 13. Order Flow

Order Flow exposes available bounded forward current state for CVD, aggressor
flow, depth/microprice/spread/liquidity, liquidations, OI, funding, basis and
cross-venue observations, including source, time, freshness and quality. It does
not expose sealed candidate profitability.

## 14. Alerts

Scanner alerts implement per-user category preferences, cooldown, dedupe,
escalation, quiet/notification toggles, and a PostgreSQL event journal. Existing
watchlist and signal-lifecycle alert services remain owned by the operational
worker. Delivery failure is isolated from scanning and the webhook.

## 15. Maintenance and migrations

Schema creation/additive migration has one production authority: the web
service's `python -m tools.run_product_migrations` pre-deploy command. A stable
PostgreSQL advisory lock serializes migration attempts; web, operational and
forward processes only wait for the committed schema/version marker. Historical
execution migration and trade-memory backfill remain in that explicit,
idempotent command. Retention is a six-hour operational-worker cycle after a
five-minute startup delay. Forward manifests/compaction remain forward-worker
duties.

## 16. Chosen Render topology

Topology B: one 512 MB web, one 512 MB operational worker, one 2 GB forward
worker, shared PostgreSQL, a 50 GB forward-only spool disk, and an immutable
S3-compatible evidence archive. The 550 GB block-disk blocker is removed.

## 17. Topology rationale

Combining the operational and forward loops saves one small worker but couples
PAPER/alert progression to websocket/book/raw-disk failures and turns any
product restart into a forward evidence gap. Separate processes also isolate
allocation spikes and event-loop latency. Functionality and evidence continuity
justify the extra worker.

## 18. Memory measurements

Local Windows startup working-set measurements were approximately: web 189.1
MB, operational worker constructed 183.3 MB, forward imports/connectors 59.0
MB, combined operational+forward import 183.6 MB. These are process working-set
RSS approximations, not idle/steady/busy or request peaks. Native NumPy/Pandas
allocations and live feed rates are not represented. Render metrics must capture
steady/high-load RSS, CPU, latency, queue growth and disk growth after deploy.

## 19. Recommended Render plans

- Web: `0.5c-512mb`, one instance, webhook concurrency capped at 20.
- Operational: `0.5c-512mb`, one instance, bounded/staggered worker startup.
- Forward: `1c-2g`, one instance, because exact frozen five-minute event
  windows cannot be count-capped without changing research semantics.
- Disk: 50 GB at `/var/data`, with a 5 GiB verified cache target and 5 GiB
  fail-closed reserve. At the measured 13.8406 GB/day combined outage rate it
  provides about 68.7 hours after cache occupancy. Normal operation remotely
  archives the 7.284 GB/day compressed canonical raw stream and compacts the
  rebuildable derived cache only after remote coverage is proven.
- Object archive: at least 30 days, approximately 218.52 GB of compressed raw
  evidence at the observed rate, plus small manifests. Cloudflare R2 Standard
  is the default recommendation; Backblaze B2 and AWS S3 remain configuration-
  compatible alternatives.
- Database: one persistent PostgreSQL database shared by all services.

## 20. Cost implications

Current Render list prices make compute approximately $39/month ($7 + $7 +
$25); the 50 GB spool is approximately $12.50/month. At steady 30-day retention,
218.52 GB costs about $3.13/month on R2 Standard after its 10 GB free tier,
about $1.45/month on B2 at $6.95/TB after its 10 GB free allowance, or about
$5.03/month on S3 Standard before requests/egress. Thus the recommended R2
shape is about $54.63/month before PostgreSQL/tax, versus roughly $176.50/month
for the 550 GB Render-disk shape. Five-minute partitions produce at most about
25,920 data objects and 25,920 manifest objects per 30 days; normal PUT/HEAD and
one full replay remain inside R2's published free operation tiers. R2 egress is
free; B2 includes egress up to three times average stored data; AWS internet
egress can dominate a full-corpus replay.

## 21. Tests

Focused forward/storage/Render/runtime/DecisionAuthority/PAPER/LIVE verification:
120 passed and one established pre-existing LIVE-readiness contract test failed.
The storage/deployment/runtime subset passed 59 tests. The Windows compacted-
SQLite atomic swap passed ten consecutive repetitions without residual temporary
files. Full suite: 650 passed, 6 failed in 82.19 seconds. Static compilation
passed. Worker capability probes passed. The final `git diff --check` passed.

## 22. Regressions

`NEW_REGRESSION = 0`. The six failures are the unchanged pre-existing version/
contract expectations for Entry Readiness v3, Alert Engine v3, PAPER Copy
Analytics v2, LIVE readiness's older gate set, and older reversal-state labels.
They are intentionally not altered in this sprint.

## 23. Remaining risks

Live steady/high-load RSS and request peaks cannot be established without a
deployment; 512 MB web/operational headroom must be observed. Both the 7.284
GB/day compressed-raw rate and 13.8406 GB/day outage rate are extrapolated from
a short 29.343-minute sample and must be reconciled after the first hosted hour
and day. Object-store credentials, endpoint behavior, upload throughput and a
real full-window recovery need deployment smoke tests. Public provider outages/
rate limits will degrade venue confirmation. The Mini App and Telegram delivery
need real-device smoke tests. The six known contract-test failures remain
technical debt. None requires weakening LIVE safety or research seals.

## 24. Files changed

The exact 39-file working-tree list is:

- `bot.py`, `database/database.py`, `render.yaml`, `requirements.txt`
- `handlers/menu.py`, `handlers/pump_scanner.py`, `handlers/research.py`,
  `handlers/terminal.py`
- `services/cache.py`, `services/forward_runtime_state.py`,
  `services/functionality_audit.py`, `services/market.py`,
  `services/market_terminal.py`, `services/operational_runtime.py`,
  `services/pump_dump_monitor.py`, `services/pump_dump_scanner.py`,
  `services/runtime_diagnostics.py`, `services/telegram_webapp.py`,
  `services/webhook_server.py`, `services/forward_object_storage.py`,
  `services/forward_evidence_archive.py`, `services/forward_partition_store.py`,
  `services/forward_event_store.py`, `services/forward_event_replay.py`
- `tools/run_operational_worker.py`, `tools/run_product_migrations.py`,
  `tools/run_forward_microstructure_collector.py`,
  `tools/replay_forward_microstructure.py`
- `tests/test_final_predeploy_product.py`,
  `tests/test_production_runtime_architecture.py`,
  `tests/test_forward_object_archive.py`,
  `tests/test_render_deployment.py`, `tests/test_render_oom_hotfix.py`
- `docs/architecture/PRODUCTION_RUNTIME_ARCHITECTURE.md`,
  `docs/architecture/TEST_BASELINE.md`,
  `docs/deployment/PRODUCTION_READINESS_REPORT.md`,
  `docs/deployment/RENDER_DEPLOYMENT.md`,
  `docs/product/FUNCTIONALITY_REGISTRY.md`

No research artifact, strategy default, `.env`, credential, raw forward
evidence, or database file is changed.

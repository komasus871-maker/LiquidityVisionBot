# Liquidity Vision Phase 0 Ground-Truth Audit

Audit date: 2026-09-12 (Asia/Jerusalem)
Audited revision: `91ae7ab69d693331669b48ad5a0882d0999ccceb` on `main`
Scope: static call-site/import tracing, startup composition review, configuration review, and local tests with all LIVE flags forced off. No exchange credentials were read or used, no network/exchange calls were made, and no database or trading behavior was changed.

The supplied brief ends mid-item at `liquidatio`. This report covers the visible A-J requirements and treats the truncated item as liquidation behavior. Anything intended after that text needs a follow-up addendum.

## Executive verdict

Liquidity Vision is not one trading system today. It is four adjacent systems sharing tables and signals:

1. a deterministic analysis and conceptual-signal lifecycle;
2. an idempotent PAPER copy-execution lifecycle;
3. a separately built BingX LIVE dispatcher/coordinator;
4. non-authoritative AI, market-intelligence, and edge-research systems.

The strongest engineering is around durable identities, fail-closed LIVE configuration, user-scoped credentials, immutable research snapshots, and recovery of ambiguous submissions. The weakest ground-truth areas are authority consistency, market-data freshness/closure semantics, simulated execution realism, and completed LIVE-order reconciliation.

The repository is not ready to claim reliable live trading or statistically validated performance. The highest-priority findings are:

- **P0 — automated signal-authority bypass:** `ObservationMonitor` calls `Analyzer` and then `SignalRecorder` without `ProbabilityEngine` or `DecisionQualityEngine`; `WatchEngine` adds probability but also omits `DecisionQualityEngine`. `SignalRecorder.record()` treats a missing `decision_gate_passed` field as `True`. Automated producers can therefore promote plans that the manual `/analyze` route would veto.
- **P0 — filled-market-order reconciliation gap:** a successful LIVE submission is persisted as `ACKNOWLEDGED`, regardless of the returned order status. Normal reconciliation compares that local order only with `adapter.open_orders()`. A quickly filled market order disappears from open orders and becomes `MISSING_EXCHANGE_ORDER`, suspending the account, while fills and local position state may remain unrecorded.
- **P0 — no single end-to-end authority:** conceptual signal state, PAPER position state, and exchange truth are separate authorities. LIVE is a second execution pipeline downstream of a PAPER copy journal, not the LIVE mode of the paper engine.
- **P1 — candle integrity is not enforced at analysis:** OKX returns UTC timestamps in a `time` column with a `RangeIndex`. `DataIntegrityEngine` checks duplicates/staleness on the index, so those checks do not apply to provider frames. The analyzer also accepts an unconfirmed candle when fewer than 220 confirmed rows remain.
- **P1 — production statistics are not fill-equivalent:** conceptual win rate/R/MFE/MAE use one-minute sampled closes, while PAPER fills are immediate and full at the planned price. PAPER slippage is recorded as metadata rather than applied to price. These are not evidence of realizable performance.
- **P1 — LIVE exit completeness is unproven:** only TP1 is attached to a LIVE order; TP2/TP3 are not represented. There is no continuous acknowledged-order poll that reliably ingests fills, and protective-order acceptance is not independently verified after parent acknowledgment.
- **P1 — release and test contracts drifted:** HEAD is labeled v10.4, but code/config/docs mostly say 10.3.0 and the README opens at v9.9.7. The suite currently has **479 passing and 6 failing** tests, predominantly stale v10.2/v10.3 contract expectations after v10.4 changes.

## A. Repository identity and startup

### Identity

| Item | Ground truth |
|---|---|
| Git HEAD | `91ae7ab69d693331669b48ad5a0882d0999ccceb` |
| Branch | `main`, tracking `origin/main`; working tree was clean before this report |
| HEAD subject | `release: LiquidityVisionBot v10.4 live pipeline and localization hardening` |
| Previous releases | `a6c47b6` v10.3, `a5ae11a` v10.2, `5cc4b9f` v10.1, `41b7e96` v10.0 |
| Code version | `version.py`: `APP_VERSION = "10.3.0"` |
| Adapter version | `BingXSwapAdapter.ADAPTER_VERSION = "10.4.0"` |
| Render version | `render.yaml`: `APP_VERSION=10.3.0` |
| Example config | `.env.example`: `APP_VERSION=10.3.0` |
| README | Starts with `v9.9.7`; later describes v10.3.0 |
| Changelog | Latest documented heading is v10.3.0 |
| Declared local runtime | `runtime.txt`: Python 3.12.10 |
| Declared Render runtime | `render.yaml`: Python 3.12.8 |

These disagreements must be resolved as a release-governance task, not silently normalized during reconstruction.

Dependencies are pinned in `requirements.txt`: aiogram 3.22.0, aiohttp 3.12.15, python-dotenv 1.1.1, ta 0.11.0, pandas 2.3.1, numpy 2.3.2, psycopg2-binary 2.9.10, and cryptography 45.0.5. There is no `pyproject.toml` or package/build metadata defining an installable application.

### Entrypoints and composition

- Primary application: `bot.py:main()`.
- Render: `render.yaml` runs `python bot.py` in webhook mode.
- Railway: `railway.json` runs `python bot.py`.
- Local documented path: `python bot.py`, normally polling.
- `config.py` loads dotenv and refuses startup without `BOT_TOKEN`.
- `bot.py:build_dispatcher()` registers the Telegram routers.
- `bot.py:main()` runs `create_tables()`, historical-execution migration, trade-memory backfill, operational retention, a database ping, and AI configuration/startup validation before constructing the bot.
- Database backend: PostgreSQL when `DATABASE_URL` starts with `postgresql://`; otherwise SQLite at `DATA_DIR/database.db`. Render requires persistent PostgreSQL through `REQUIRE_PERSISTENT_DB=true`.
- `database/database.py:create_tables()` is a large additive bootstrap/migration function containing 92 `CREATE TABLE IF NOT EXISTS` statements, column additions, data repairs, and indexes. `schema_migrations` exists, but startup still owns broad schema mutation rather than a separate ordered migration runner.

Nine in-process workers are always constructed and their `run_forever()` coroutines are always scheduled:

1. `SignalTracker`
2. `ObservationMonitor`
3. `WatchEngine`
4. `CopyExecutionWorker`
5. `AIShadowWorker`
6. `ResearchWorker`
7. `MicrostructureObserver`
8. `LiveReconciliationWorker`
9. `LiveCopyWorker`

Feature-disabled workers generally remain inert. `LiveCopyWorker` defaults disabled through `LIVE_DISPATCHER_ENABLED`; the checked Render manifest also sets `LIVE_EXECUTION_ENABLED=false`, `LIVE_EXCHANGE_BINGX_ENABLED=false`, `BINGX_PRODUCTION_ADAPTER_ALLOWED=false`, `ALLOW_USER_LIVE_CONNECTIONS=false`, and `AI_TRADING_MODE=AI_OFF`.

There is no separate scheduler framework. Scheduling consists of worker sleep loops plus a webhook-only `/internal/monitor` endpoint in `services/webhook_server.py`. The endpoint calls every worker's `check_once()` while their loops also exist. Distributed leases prevent most duplicate cycles; `AIShadowWorker.check_once()` has no worker lease/local cycle lock, although provider concurrency, request claims, and decision idempotency reduce duplicate effects.

## B. Real runtime architecture

```text
bot.py
├─ Telegram routers
│  ├─ /analyze → Market → Analyzer → MarketContext → Probability → DecisionQuality → SignalRecorder
│  ├─ watch commands → user_watchlist
│  ├─ PAPER copy/profile/panic commands
│  └─ exchange/readiness/LIVE enable/emergency-close commands
├─ SignalTracker → conceptual signal activation/TP/stop lifecycle
├─ ObservationMonitor → Market → Analyzer ───────────────→ SignalRecorder   [gate bypass]
├─ WatchEngine → Market → Analyzer → Probability ───────→ SignalRecorder   [gate bypass]
├─ CopyExecutionWorker → CopyTradingService → Planner → PAPER engine/lifecycle
├─ AIShadowWorker → AI observations/decisions/outcome evaluation only
├─ ResearchWorker → immutable snapshots/outcomes → EdgeDiscovery only
├─ MicrostructureObserver → BingX public REST aggregates only
├─ LiveReconciliationWorker → exchange-vs-local comparison
└─ LiveCopyWorker → copy journal → LIVE queue → LiveCopyDispatcher
                                  → LiveExecutionCoordinator → BingXSwapAdapter.place_order
```

### Major component classification

| Component | Classification | Evidence |
|---|---|---|
| `bot.py:main` | AUTHORITATIVE_RUNTIME | Production entrypoint; constructs all routers and workers. |
| `services.market.Market` / `OKXProvider` | AUTHORITATIVE_RUNTIME | Sole candle provider used by analysis and signal tracking. |
| `services.unified_core.pipeline.UnifiedAnalysisPipeline` | AUTHORITATIVE_BUT_DELEGATING | Global `unified_pipeline`, called by `Analyzer`; delegates feature stages. |
| `services.analyzer.Analyzer` | AUTHORITATIVE_RUNTIME | Called by `/analyze`, scanner, watch, observation, and other analysis services; chooses direction/status and creates the base plan. |
| `TradePlanIntegrity` | AUTHORITATIVE_RUNTIME | `Analyzer` calls `apply()` and it overwrites entry/stop/TPs with a valid 1R/2R/3R geometry. |
| `MarketContextEngine` | SUPPORTING | Manual `/analyze` only; modifies setup/readiness from BTC context. |
| `ProbabilityEngine` | SUPPORTING | Manual `/analyze` and watch; supplies similarity/history used by `UnifiedDecisionEngine`, but its output is not itself the recorder gate. |
| `DecisionQualityEngine` | AUTHORITATIVE_RUNTIME | Sets `decision_gate_passed`, but only manual analyze and scanner call it; watch/observation omit it. |
| `UnifiedDecisionEngine` | SUPPORTING | Produces TAKE/WAIT/SKIP/INVALID inside `DecisionQualityEngine`; `SignalRecorder` does not inspect this action. |
| `ConvictionEngine` | SUPPORTING | Produces an action that is assigned to `system_decision` and then overwritten by `DecisionBrain`. |
| `DecisionBrain` | SUPPORTING | Turns unified output into a presentation/EV report; no order authority. |
| `MarketMemory` | SUPPORTING | Persists/retrieves context during decision-quality enrichment. |
| `MarketIntelligenceEngine` | RESEARCH_ONLY | Executed after Analyzer's decision fields are final; envelope says no economic/execution authority. |
| `SignalRecorder` | AUTHORITATIVE_RUNTIME | Converts eligible analysis into durable conceptual signals/observations; contains thresholds and conflict checks. |
| `SignalTracker` | AUTHORITATIVE_RUNTIME | Owns conceptual activation, stop/TP state, sampled R, MFE/MAE, and terminal events. |
| `CopyExecutionPlanner` / `ExecutionValidator` | AUTHORITATIVE_RUNTIME | Authoritative PAPER copy admission, sizing, leverage, and deterministic plan identity. |
| `CopyExecutionEngine` | AUTHORITATIVE_RUNTIME | Sole PAPER adapter dispatch coordinator; hard-blocks non-PAPER modes. |
| `PaperExecutionLifecycle` | AUTHORITATIVE_RUNTIME | Durable PAPER order/fill/position and accounting authority. |
| `paper_positions` projection | COMPATIBILITY_FACADE | Updated from `paper_execution_positions`; explicitly non-authoritative. |
| `LiveCopyDispatcher` / `LiveExecutionCoordinator` | AUTHORITATIVE_RUNTIME | Separate LIVE authorization, persistence, and submit boundary; configured disabled in the audited manifest. |
| `BingXSwapAdapter.place_order` | AUTHORITATIVE_RUNTIME | Only implemented production economic order adapter; configured disabled in the audited manifest. |
| Binance/Bybit/OKX authenticated adapters | SUPPORTING | Health, account, balances, rules, open orders, positions; no production `place_order()` override. |
| `AITradingService` / `AIShadowWorker` | RESEARCH_ONLY | Observes existing signals and persists advisory decisions; no consumer in planner/execution. |
| `ResearchEngine` / `EdgeDiscoveryEngine` | RESEARCH_ONLY | Runtime worker persists and evaluates research records; explicit `execution_authority=false`. |
| `core.backtest_engine.BacktestEngine` | TEST_ONLY | Research utility with no non-test production call site. |
| `services.brain.Brain` | COMPATIBILITY_FACADE | Wrapper for `DecisionBrain`, constructed only by unused container/scanner-engine modules. |
| `services.scanner_engine.ScannerEngine`, `scanner_v2.ScannerV2`, `services.container` | APPARENTLY_DEAD | No production import/construction site found. |
| `services.decision_engine`, `services.scoring_engine`, `services.trade_planner` | LEGACY_BUT_STILL_REFERENCED | Referenced by the alternate `core/engine` stage graph, not production startup. |
| `core/engine/*` | TEST_ONLY | Importable legacy alternate pipeline, but not wired by `bot.py` or handlers. |
| `core.pipeline` and old `core.*Engine` modules | APPARENTLY_DEAD | No production call sites; `import core.pipeline` fails because `core.scoring` does not exist. |

## C. Trading authority matrix

1. **Trade-idea origin:** `Analyzer.analyze()` originates the directional setup when invoked by `handlers/analyze.py:_run_analysis`, `WatchEngine._analyze_one`, or `ObservationMonitor.check_once`. Scanner also generates analyses but does not record a signal.
2. **LONG/SHORT/NO-TRADE:** `Analyzer.analyze()` compares long/short scores and always chooses LONG or SHORT, even in the neutral-edge branch. No-trade is represented later as plan/status/action: Analyzer can mark `PLAN INVALID`; `DecisionQualityEngine.enrich()` can make the item non-actionable; `UnifiedDecisionEngine.evaluate()` emits SKIP/INVALID.
3. **Vetoes:** plan geometry (`TradePlanIntegrity`), decision gate (`DecisionQualityEngine`, when called), signal eligibility/threshold/portfolio conflicts (`SignalRecorder`), PAPER profile/training/portfolio checks (`ExecutionValidator`), PAPER contract/mode checks (`ExecutionValidationPipeline`), LIVE settings/recovery/preflight/reconciliation/PnL/risk/sizing/kill switches (`LiveCopyDispatcher`), durable production gates (`LiveExecutionCoordinator`), and exchange validation (`BingXSwapAdapter`).
4. **Intended entry:** `TradePlanIntegrity.build()` chooses current price for READY or the preferred-zone midpoint for planned entries. `CopyExecutionPlanner.build()` may use `market_price/current_price` instead of the locked signal entry. Actual LIVE entry is exchange fill truth.
5. **Stop loss:** `TradePlanIntegrity.build()` constructs the signal stop from raw stop, zone, ATR, and buffers. PAPER stores it but exits based on `SignalTracker`; LIVE passes it to BingX as an attached stop.
6. **Take profit/exit logic:** `TradePlanIntegrity` creates exact 1R/2R/3R targets. `SignalTracker._update()` owns conceptual TP/stop transitions. `PaperExecutionLifecycle.apply_signal_transition()` reduces to 50% after TP1, 25% after TP2, then zero at terminal state. LIVE sends only the first TP plus the stop; no LIVE TP2/TP3 lifecycle is wired.
7. **Position size:** PAPER: `ExecutionValidator.validate()` and its position-sizing service. LIVE: `LiveSizer.calculate()` using settings, risk profile, balances, and symbol rules.
8. **Leverage:** PAPER plan takes `RiskProfile.leverage`. LIVE applies settings/risk/global ceilings and `BingXSwapAdapter.set_leverage()` configures both hedge sides.
9. **Execution authorization:** PAPER requires an approved deterministic `CopyExecutionPlan`, validation pipeline, and journal claim. LIVE additionally requires an enabled account/settings, dispatcher flag, recovery READY, fresh preflight, clean reconciliation, current daily PnL, active risk profile, kill-switch clearance, a claimed approved copy plan, environment/feature flags, production-adapter permission, and an unexpired VST certification.
10. **Order submission:** PAPER uses `PaperExecutionAdapter.execute()` (synthetic). Production LIVE uses `LiveExecutionCoordinator.submit()` → `BingXSwapAdapter.place_order()`. Emergency closes use the same coordinator with a distinct reduce-only authority source.
11. **Intended-order record:** PAPER uses `copy_execution_journal` then `paper_execution_orders`. LIVE uses `live_execution_queue`, `live_order_intents`, and `live_executions` before the adapter call (`LiveExecutionRepository.create`).
12. **Exchange-confirmed-order record:** `LiveExecutionCoordinator.submit()` stores `exchange_order_id` and moves the row to `ACKNOWLEDGED` after adapter return. It does not honor/validate the returned exchange status at this point.
13. **Fills:** PAPER: `PaperExecutionLifecycle.record_fill()`. LIVE: `LiveExecutionRepository.ingest_fills()`, called during ambiguous-submission recovery and only limited reconciliation cases; there is no complete normal ACK polling path.
14. **Position authority:** PAPER: `paper_execution_positions`; `paper_positions` is a legacy projection. LIVE: the exchange is intended to be authoritative (`adapter.positions()`), while `live_positions` is a local fill-derived ledger. The current ingestion gap prevents the local ledger from being a reliable mirror.
15. **Close/reduce:** conceptual: `SignalTracker`. PAPER: `PaperExecutionLifecycle.apply_signal_transition()` and `close_position()`; `/panic` also uses the lifecycle. LIVE: attached exchange stop/TP and `LiveEmergencyCloseService.confirm()` for user-confirmed reduce-only market closure. No full strategy-managed LIVE multi-target exit loop exists.
16. **Realized PnL:** conceptual signals store sampled `realized_r` in `SignalHistory`; PAPER calculates quantity × price move in `PaperExecutionLifecycle`; LIVE daily PnL uses exchange fills through `LiveDailyPnlService`.
17. **Unrealized PnL:** PAPER `mark_to_market()`/portfolio accounting uses last known price. LIVE uses exchange position `unrealized_pnl` in daily PnL refresh. Conceptual portfolio output is an R-risk approximation, not money PnL.
18. **Fees:** backtest applies configured entry/exit fee rates. PAPER entry and normal lifecycle exits use the default 0.05% commission and persist a ledger; an initial catch-up transition can use the method's zero default. LIVE ingests exchange commissions when fills are actually ingested.
19. **Slippage:** `ExecutionValidator` gates planned-vs-current deviation. PAPER stores `slippage_pct` but fills at the unadjusted plan entry; backtest subtracts a fixed slippage cost; LIVE records modeled slippage for risk but does not calculate actual reference-to-fill slippage.
20. **Reconciliation:** `LiveReconciliationService.reconcile()` and `LiveReconciliationWorker`. The exchange is treated as truth, mismatches suspend the account and activate a kill switch, but completed-order discovery is incomplete.
21. **Can Telegram/manual execution bypass risk?** There is no manual Telegram command that creates a production LIVE entry. `/closeall` changes conceptual signals, `/panic` reduces PAPER positions, and two-step LIVE emergency close bypasses the normal entry planner by design but is ownership-checked and reduce-only. These are exposure-reducing paths, not entry-risk bypasses.
22. **Can copy trading bypass normal execution?** The designed PAPER path cannot bypass its planner/journal/validator/engine. LIVE can only originate from an approved/claimed copy journal. However, its upstream source signal can come from watch/observation paths that bypass `DecisionQualityEngine`, so end-to-end decision policy is bypassable even though execution plumbing is not.
23. **Can AI/research/intelligence trigger execution?** No direct call path was found. AI consumes already-recorded signals; research and market-intelligence outputs explicitly deny execution authority; LIVE payloads include `ai_authority=false`; production coordinator requires `DETERMINISTIC_APPROVED_PLAN`. Historical similarity can affect a displayed unified score, but that unified action is not the recorder's authorization field.
24. **Can multiple components create conflicting decisions?** Yes at both route and representation levels: Analyzer status/direction, DecisionQuality gate/action, Conviction action, UnifiedDecision action, DecisionBrain action, and AI recommendations can disagree. `system_decision` is assigned from conviction and then overwritten by DecisionBrain. More seriously, manual/watch/observation routes apply different layers before recording.
25. **Single authoritative execution path?** No. PAPER and LIVE have separate dispatchers, ledgers, and state machines; conceptual signals are a third lifecycle. Within certified BingX LIVE entry, coordinator → BingX adapter is the intended single submission boundary, but emergency close is a separate authorized route and reconciliation does not yet complete the authority chain.

## D. Actual trade lifecycles

### Manual analysis and conceptual signal

```text
Telegram /analyze
→ handlers/analyze._run_analysis
→ Market.get_klines → OKXProvider REST history-candles
→ Analyzer.analyze
   → UnifiedAnalysisPipeline feature stages
   → long/short scoring + execution status
   → TradePlanIntegrity.apply
   → MarketIntelligenceEngine [research envelope]
→ MarketContextEngine [BTC adjustment]
→ ProbabilityEngine [historical/similar cases]
→ DecisionQualityEngine [decision_gate_passed]
→ SignalRecorder.record
→ analysis_observations + signals
→ SignalTracker polls 1m sampled close
→ WATCHING/TRIGGERED/ACTIVE/TP1/TP2/TP3/STOP/BREAKEVEN/etc.
→ signal_events + conceptual R/MFE/MAE/statistics
```

### Automated watch/observation divergence

```text
WatchEngine:       Market → Analyzer → Probability ─────────→ SignalRecorder
ObservationMonitor: Market → Analyzer ──────────────────────→ SignalRecorder
                                                     missing DecisionQuality
SignalRecorder: analysis.get("decision_gate_passed", True)
```

This is an actual authority bypass, not only duplicate presentation logic.

### PAPER copy

```text
eligible ACTIVE conceptual signal + enabled PAPER profile
→ CopyTradingService.sync_all
→ CopyExecutionPlanner
→ ExecutionValidator (training/profile/portfolio/size/slippage/activation)
→ CopyExecutionJournal reserve + atomic claim
→ ExecutionValidationPipeline (including PAPER-only gate)
→ ExecutionDispatcher
→ PaperExecutionAdapter [immediate synthetic success]
→ PaperExecutionLifecycle
   → paper_execution_orders
   → one immediate full fill at plan.entry_price
   → paper_execution_fills
   → paper_execution_positions [authority]
   → paper_positions [compatibility projection]
→ later conceptual signal transitions
→ PaperExecutionLifecycle partial/terminal reductions
→ paper_portfolio_ledger → PAPER analytics
```

SHADOW in the AI subsystem means post-signal advisory observation. `ExecutionMode.SHADOW` in `LiveExecutionCoordinator` persists an intent but returns without an economic call. Neither is a realistic broker simulation.

### LIVE copy entry

```text
copy_execution_journal status EXECUTING/EXECUTED + LIVE account/settings
→ LiveCopyWorker.discover
→ immutable market-intelligence quality lookup
→ live_execution_queue (durable claim)
→ LiveCopyDispatcher.process_claimed
   → settings eligibility
   → restart recovery + fresh preflight
   → kill switches
   → exchange reconciliation must MATCH
   → authoritative daily PnL refresh
   → active risk profile
   → balances + symbol rules
   → LiveSizer
→ sealed immutable ExchangeOrderRequest (MARKET + stop + TP1 only)
→ LiveExecutionCoordinator
   → approved-plan identity match
   → environment/flags/certification/limits/risk gates
   → live_order_intents + live_executions persisted
   → CAS state transitions + attempt record
→ BingXSwapAdapter.normalize_order
→ set margin mode/leverage
→ POST parent order with attached stop/TP
→ local ACKNOWLEDGED
→ [missing reliable normal fill/order-status ingestion]
→ reconciliation/open-orders comparison
```

### Manual closures

- `/trade … close` and `/closeall`: close/cancel conceptual signals in `SignalHistory`; PAPER copy catches up later through its lifecycle.
- `/panic`: disables PAPER copying and closes the user's PAPER positions at their last stored price.
- `/live_emergency_close` → `/live_emergency_confirm TOKEN`: private-chat, user-owned, expiring two-step snapshot; rechecks positions and submits reduce-only market closures through `LiveExecutionCoordinator`.

## E. Decision-stack audit and disposition

| Component | File | Constructed/called by | Output/persistence | Trading impact and overlap | Future disposition |
|---|---|---|---|---|---|
| Unified feature pipeline | `services/unified_core/pipeline.py` | Global instance; `Analyzer` | Feature context + cache | Canonical feature source; overlaps old core engine | KEEP_AND_HARDEN |
| Analyzer | `services/analyzer.py` | Analyze/watch/observation/scanner services | Direction, scores, status, raw plan, context | Primary idea/direction authority | KEEP_AND_HARDEN |
| TradePlanIntegrity | `services/trade_plan_integrity.py` | Analyzer, recorder/validator checks | Rewrites entry/SL/TP to 1/2/3R | Actual deterministic plan authority despite Analyzer's earlier ATR plan | KEEP_AND_HARDEN |
| MarketContextEngine | `services/market_context.py` | Manual analyze | BTC-adjusted setup/readiness | Route-specific; not applied to automated producers | MERGE |
| ProbabilityEngine | `services/probability_engine.py` | Manual analyze/watch/notifier | Similarity/history; reads signals | Historical outcomes feed unified score; overlaps research statistics | KEEP_AND_HARDEN |
| DecisionQualityEngine | `services/decision_quality.py` | Manual analyze and scanner | Gate, actions, presentation | Only its `decision_gate_passed` affects recorder; omitted by two producers | KEEP_AND_HARDEN |
| UnifiedDecisionEngine | `services/unified_decision.py` | DecisionQuality | TAKE/WAIT/SKIP/INVALID | Advisory because recorder ignores action; overlaps gate/conviction | MERGE |
| ConvictionEngine | `services/conviction_engine.py` | DecisionQuality | Conviction/action | `system_decision` output immediately overwritten | MERGE |
| DecisionBrain | `services/decision_brain.py` | DecisionQuality | User-facing action/EV | Reformats unified action; overlaps report/EV layer | MERGE |
| ExpectedValueEngine | `services/expected_value.py` | DecisionBrain | EV display | Depends on conceptual outcomes; not execution gate | RESEARCH_ONLY |
| MarketMemory | `services/market_memory.py` | DecisionQuality | Remembered setup context | Supporting historical context | KEEP_AND_HARDEN |
| MarketIntelligenceEngine | `services/market_intelligence.py` | Analyzer, product handlers | Quality/readiness/story/research | Runs after base decision; explicit non-authority; version drift in tests | RESEARCH_ONLY |
| SignalRecorder | `services/signal_recorder.py` | Analyze/watch/observation | observations/signals | Real promotion authority; permissive missing-gate default | KEEP_AND_HARDEN |
| SignalTracker | `services/signal_tracker.py` | Startup worker | signal lifecycle/R/MFE/MAE | Conceptual outcome and indirect PAPER-exit authority | REPLACE_LATER |
| Copy planner/validator | `services/copy_execution_planner.py`, `services/execution_validator.py` | CopyTradingService | Approved/rejected immutable plan | PAPER admission, sizing, risk; source for LIVE | KEEP_AND_HARDEN |
| CopyExecutionEngine | `services/copy_execution_engine.py` | Execution queue/CopyTradingService | Journal terminal result | PAPER-only dispatch authority | KEEP_AND_HARDEN |
| PaperExecutionLifecycle | `services/paper_execution_lifecycle.py` | Copy engine/service | Orders, fills, positions, ledger | PAPER authority; simulation model is unrealistic | KEEP_AND_HARDEN |
| LiveCopyDispatcher | `services/live_copy.py` | LiveCopyWorker | queue/request/result | LIVE admission/sizing before coordinator | KEEP_AND_HARDEN |
| LiveExecutionCoordinator | `services/live_execution.py` | Live dispatcher/emergency/demo certification | intents/executions/attempts/fills | Production submit/recovery authority; missing normal fill loop | KEEP_AND_HARDEN |
| AITradingService / AIShadowWorker | `services/ai_trading.py` | Startup + AI handlers | `ai_decisions`, evaluation data | Advisory only; no planner consumer | RESEARCH_ONLY |
| ResearchEngine / EdgeDiscoveryEngine | `services/research_engine.py`, `services/edge_discovery.py` | ResearchWorker/handlers | immutable snapshots, outcomes, findings/models | No execution imports/authority | RESEARCH_ONLY |
| AlphaResearchEngine | `services/alpha_research.py` | Research handlers/tests | exports/summaries | Compatibility exporter; labels legacy rows unverified | KEEP |
| BacktestEngine | `core/backtest_engine.py` | Tests/manual utility only | in-memory report | Isolated OHLC research utility, not production validator | REPLACE_LATER |
| Brain facade | `services/brain.py` | unused container/scanner_engine | DecisionBrain wrapper | Compatibility only | REMOVE_AFTER_MIGRATION |
| Alternate scanner engines | `services/scanner_engine.py`, `services/scanner_v2.py` | no caller found | analysis outputs | Duplicate unused routes | REMOVE_AFTER_MIGRATION |
| Old services decision/scoring/planner | `services/decision_engine.py`, `services/scoring_engine.py`, `services/trade_planner.py` | alternate core stages | legacy decisions/plans | Not production, but referenced by alternate graph | DEPRECATE |
| Alternate core engine | `core/engine/*` | tests/no startup wiring | stage context | Parallel architecture | REMOVE_AFTER_MIGRATION |
| Broken old pipeline/core engines | `core/pipeline.py`, `core/desicion.py`, `core/*_engine.py` | no production caller | unavailable/legacy | `core.pipeline` import fails on missing `core.scoring` | REMOVE_AFTER_MIGRATION |

## F. Market-data integrity

### What exists

- Analysis candles: public OKX perpetual-swap REST `history-candles`, through `services/providers/okx.py`.
- Timeframes: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 1w; 50-1000 requested rows.
- Symbols: normalized to base asset and resolved to active `BASE-USDT-SWAP` instruments; active instrument metadata is cached for five minutes.
- Candle pages are deduplicated by timestamp, sorted oldest-to-newest, numerically parsed, and timestamped as UTC in the `time` column.
- Request behavior: 10-second total timeout, bounded attempts (Render sets two), retry on 429/5xx/client/timeout with simple 0.25-second linear backoff.
- Cache: in-process 20-second frame cache keyed by raw symbol/interval/limit.
- Microstructure: optional `MicrostructureObserver` uses credential-free BingX REST depth, funding, and open-interest snapshots, persists bounded aggregates/source health, and is disabled by default in the example config.
- Authenticated exchange adapters expose account/order/position/balance/rules data for Binance, Bybit, BingX, and OKX. These are not the analysis candle source.

### What does not exist or is not enforced

- No websocket candle/order-book/trade stream was found.
- No provider fallback for analysis candles.
- No shared/global rate limiter or `Retry-After` handling in the OKX public provider; each request creates a new `ClientSession`.
- No candle-gap detection, expected cadence validation, future-timestamp rejection, source sequence number, or cross-provider reconciliation.
- No trade/tick tape, aggressor-volume delta, liquidation-event feed, or basis/mark-index basis feed. “Liquidation” text in intelligence is not backed by a liquidation data source. Exchange positions may expose their own liquidation price, which is a different datum.
- Order-book data is sampled/aggregated and not stored as a raw replayable book. It is research/observability input, not execution input.
- Funding and OI are separate BingX observations, not guaranteed time-aligned with OKX analysis candles.

### Silent bad-data paths

1. Provider frames keep `RangeIndex`; `DataIntegrityEngine.validate_market_frame()` checks duplicate timestamps and staleness only through the index. Its stale/duplicate timestamp protections therefore do not operate on normal OKX frames.
2. The normal analysis path does not call `validate_market_frame()` before Analyzer. Only `SignalTracker` applies it to six 1m rows, where the RangeIndex still disables staleness checking.
3. `UnifiedAnalysisPipeline._prepare_frame()` filters `confirm == "1"` only when at least 220 confirmed rows remain. With a smaller/sparse input it silently keeps the unconfirmed current candle.
4. Missing bars can pass because only NaN OHLC and impossible geometry are checked.
5. A future-dated `time` column can pass analysis and tracking.
6. Backtests accept caller-provided frames and do not enforce provider provenance, UTC timestamps, candle confirmation, gaps, or the production provider's exact semantics. Backtest and LIVE/analysis inputs are therefore not semantically equivalent by construction.

## G. Research and backtesting validity

### Existing protections

- `core.backtest_engine.BacktestEngine` passes a strategy only `frame[:signal_index+1]` and begins entry search on the next bar, which prevents direct future-candle access through its argument.
- OHLC same-candle stop/next-target ambiguity has an explicit conservative policy.
- Default backtest costs include 5 bps each side in fees and 3 bps each side in modeled slippage; optional constant funding exists.
- Edge research uses immutable/checksummed decision-time snapshots, a feature whitelist, forbidden outcome fields, versioned normalized features, explicit late-backfill labeling, and manual-intervention exclusions.
- Edge Discovery uses chronological walk-forward folds (default 60 train/20 validation), deterministic bootstrap intervals, sample tiers, frozen hypotheses, and post-cutoff forward cohorts (default minimum 30).
- Outputs remain exploratory/research-only and deny execution authority.

### Validity gaps

| Risk | Current state |
|---|---|
| Lookahead/future candles | Backtest slicing helps, but input provenance and strategy external state are not constrained. Incomplete current candles can already enter production analysis. |
| Survivorship bias | No point-in-time symbol universe, delisting history, or eligibility history. |
| Selection bias | Research observes promoted/eligible conceptual signals, not all contemporaneous opportunities. The gate bypass also changes the population by route. |
| Outcome truth | Outcomes inherit sampled-close conceptual lifecycle and PAPER simulation, not independent tick/candle replay or authoritative exchange fills. |
| Overfitting/parameter mining | Combination tests are capped and labeled exploratory, but `bonferroni_alpha` is only recorded; it is not applied to interval/promotion logic. Top findings can be repeatedly selected and frozen. |
| Same-period optimization/evaluation | Frozen forward cohorts and walk-forward folds exist. The standalone backtest has no optimizer/holdout protocol, and exploratory rankings still use the available history. |
| Unrealistic fills | Entry is declared filled whenever a bar range contains the limit price; no queue, spread, liquidity, volume, partial fill, or gap-through price. |
| Intrabar ordering | Conservative only for stop versus the next target. Entry timing within the entry bar is unknown; MFE/MAE use the whole bar, including extremes that may precede entry. Only one target advances per backtest bar. |
| Costs | Backtest fixed fees/slippage exist; spread and latency do not. Funding defaults to zero and is constant when enabled. No liquidation/margin model. |
| Regime overfitting | Regime grouping/control exists, but small overlapping cohorts and repeated selection are still exploratory. |
| Tiny samples | Tiers and minimums exist (default discovery 20, moderate 50, high 100, forward 30), but some descriptive output is produced from as few as three samples. |

The backtest has no production call site and is not what `ResearchWorker` runs. Its existence does not validate the production strategy.

## H. Performance/statistics truthfulness

| Metric family | Source and implementation | Truth assessment |
|---|---|---|
| Conceptual win rate | `SignalHistory.get_stats()` over resolved conceptual signals; wins/(wins+losses), excluding break-even from denominator | Sampled signal outcome, not fill performance; exclusion should be explicit in UI. |
| Conceptual R/expectancy/PF | `SignalHistory` and `PerformanceIntelligence` use stored `realized_r` | SignalTracker sampled-close lifecycle; no fees/funding/slippage. PF uses `999` when there are profits and no losses, which is display-misleading. |
| Conceptual MFE/MAE | SignalTracker updates max/min from sampled 1m closes | Misses intrabar highs/lows and gaps; not true excursion. |
| PAPER gross/realized/unrealized | `PaperExecutionLifecycle` and unified accounting | Quantity-based, but synthetic prices and sparse mark updates limit realism. |
| PAPER fees/net PnL | Position commission totals and ledger | Generally includes 0.05% entry/exit fees; catch-up edge cases can omit exit commission. “Actual” means actual simulation ledger, not exchange reality. |
| PAPER slippage | Average of `paper_execution_fills.slippage_pct` | Metadata only; fill price is not adjusted, so net PnL does not actually bear recorded slippage. |
| Backtest metrics | `BacktestEngine._metrics()` | Net R, expectancy, PF, max drawdown R, losing streak, avg win/loss/MFE/MAE, and `sharpe_like`; not an annualized return Sharpe. No Sortino/exposure there. |
| Edge research metrics | `StatisticalResearch.metrics()` | Expectancy/PF/drawdown proxy/MFE/MAE and Sharpe-like/Sortino-like after n≥30; based on conceptual outcomes. |
| LIVE PnL | `LiveDailyPnlService` from exchange fills (configured symbols, bounded endpoint history) plus exchange positions | Best intended authority, but incomplete fill ingestion and a seven-day BingX fills window can make local history incomplete; empty symbol scope fails closed. |
| Strategy/symbol/timeframe | Performance/edge/copy cohort services | Present, but mixes conceptual/simulated sources depending screen. |
| Regime statistics | Edge research/backtest grouping | Research only. Primary user performance does not give exchange-fill regime PnL. |
| Exposure | PAPER portfolio accounting and LIVE exchange positions | PAPER is simulated; conceptual performance “portfolio” views are R-risk approximations. |

Important labels needing correction in a later phase: `actual_net_pnl` in PAPER UI, PF `999`, “probability” derived from selected conceptual signals, and “Sharpe” variants that are unannualized trade-R ratios.

## I. LIVE execution audit

### Controls that are present

- Fernet-encrypted, versioned credential keyring; credentials are scoped by Telegram user and exchange. This audit did not read them.
- Private-chat confirmation and separate explicit LIVE enablement.
- Environment flags, adapter allowlist, user-connection flag, account enable/kill state, VST certification, and fresh preflight.
- Readiness checks for trading permission, withdrawal disabled/unresolved, balances, risk completeness, time sync, symbol rules, portfolio/reconciliation/PnL state, limits, and adapter capabilities.
- Balance-based sizing; symbol quantity/price rounding; minimum/maximum quantity, minimum notional, max leverage, position-mode, margin-mode, and reduce-only checks.
- Global/account risk ceilings for positions, order notional, portfolio/symbol exposure, daily realized/total loss, modeled slippage, leverage, and cooldown.
- Durable plan/queue/request identities, deterministic client order IDs, pre-call intent/execution/attempt persistence, CAS state transitions, queue leases, and timeout-to-UNKNOWN behavior.
- Ambiguous submission recovery queries by client ID and never blindly resubmits while truth is unknown.
- Mismatches fail closed by suspending the account and setting a kill switch.

### Critical gaps and restart outcomes

1. **Normal fill discovery:** successful adapter return always becomes local `ACKNOWLEDGED`. Recovery queries/fill ingestion run only for `UNKNOWN` or `RECOVERY_REQUIRED`. Reconciliation starts from `open_orders()`, so a completed market order is interpreted as missing instead of queried from order history. Result: a legitimate fill can cause suspension, a forgotten local fill/position, wrong local PnL, and later orphan-position alarms.
2. **Returned status ignored:** coordinator records ACKNOWLEDGED even if the adapter's parsed order reports FILLED/PARTIAL/CANCELLED/REJECTED. The exchange API may normally return errors separately, but the local contract does not enforce status truth.
3. **Protective exits:** parent request includes attached SL and only TP1. TP2/TP3 are dropped. After acknowledgment, code does not query and prove the attached stop/TP exist and match. A crash after parent acceptance is protected only if the exchange actually accepted the attached fields.
4. **Position ledger:** `live_positions` depends on ingested fills; because normal fills can be missed, it cannot presently be the restart authority. Exchange positions are authoritative but are used primarily to detect mismatches rather than reconstruct safely.
5. **Order polling/stale orders:** there is no general acknowledged-order status poll/terminal transition loop. Cancel logic exists, but no production stale-order sweeper is wired. Current entries are MARKET, reducing but not eliminating this concern.
6. **Partial fills:** modeled and persisted only when recovery/reconciliation ingests fills. There is no reliable continuous partial-fill progression for normal submissions.
7. **Concurrency/races:** deterministic keys, unique rows, claims, leases, and CAS transitions are good defenses. Safety still depends on exchange enforcement of client-order-id uniqueness and successful query-by-client-id after ambiguous timeouts.
8. **Non-BingX LIVE:** registry names four exchanges, but the reconciliation worker explicitly treats non-BingX LIVE as uncertified/suspendable and only BingX implements the production order path. UI/provider breadth must not be confused with LIVE execution support.

Restart matrix:

| Crash point | Expected behavior | Residual risk |
|---|---|---|
| Before intent persistence | No exchange call; rediscovery can rebuild | Low |
| After intent persistence, before submit claim | Stable plan/queue can resume | Low |
| After SUBMITTING, before/inside exchange call | Startup recovery converts to UNKNOWN and queries client ID | Safe only if exchange query is available and client ID is unique |
| Exchange accepted, process crashed before local ACK | Same UNKNOWN recovery path should find it and ingest fills | Query outage leaves execution blocked, correctly fail-closed |
| Local ACK, exchange quickly FILLED | Normal recovery skips it; reconciliation can mark missing | **High: forgotten local fill/position and account suspension** |
| Parent accepted but attached protection absent/mismatched | No independent protection verification | **High: missing protective exit possible** |

## J. PAPER and SHADOW realism

### PAPER copy simulation

| Behavior | Current implementation | Realism |
|---|---|---|
| Spread | None | Missing |
| Slippage | Planned deviation is gated and stored; fill price unchanged | Cosmetic, not economic |
| Commissions | 0.05% default entry and normal exits | Basic fixed model |
| Funding | None in PAPER lifecycle | Missing |
| Latency | None | Missing |
| Partial fills | Lifecycle API supports them, but default adapter always produces one immediate full fill | Not simulated |
| Candle ambiguity | PAPER copy follows SignalTracker sampled closes, not OHLC path | Avoids explicit ambiguity by losing intrabar truth |
| Gaps | Uses observed close as activation/exit; no gap-through fill policy | Unrealistic |
| Stop | No resting simulated stop; checked once per signal-tracker cycle against latest close | Can miss wicks and materially over/understate loss |
| TP | Checked against latest close; TP3 then TP2 then TP1 | Can jump directly across levels; fill uses sampled/current price, not target/queue truth |
| Liquidation/margin | No liquidation engine, maintenance margin, mark-price trigger, or bankruptcy model | Missing |

`/panic` closes at the last stored PAPER price, not a fresh executable bid/ask. PAPER position state is durable and idempotent, but persistence quality is not simulation realism.

### Backtest simulation

The backtest is more explicit than PAPER about fixed costs and OHLC ambiguity, but still lacks spread, order-book liquidity, latency, queue priority, partial fills, gap execution, dynamic funding, margin/liquidation, and exchange rules. A bar merely touching entry causes a fill. It cannot serve as a fill model for LIVE.

### AI/SHADOW

- `AIShadowWorker` observes existing signals and stores AI recommendations/counterfactual evaluations; it does not create orders or modify deterministic plans.
- `ExecutionMode.SHADOW` persists an execution record but returns before adapter submission.
- Neither path simulates broker fills, balances, positions, costs, or latency. “Shadow” means non-economic observation, not forward execution simulation.

## Test baseline

Command environment explicitly forced `LIVE_EXECUTION_ENABLED=false`, `LIVE_DISPATCHER_ENABLED=false`, `BINGX_PRODUCTION_ADAPTER_ALLOWED=false`, and used a dummy bot token. The repository-local dependency bundle was added to `PYTHONPATH`; no dependencies were installed.

Result: **479 passed, 6 failed in 72.00 seconds**.

Failures:

1. `test_market_intelligence_v102_separates_scores_and_fusion`: expected `entry-readiness-v3`, code returns v4.
2. `test_alert_engine_v3_records_usage_delivery_and_unchanged_state`: expected alert-engine-v3, code returns v4.
3. `test_copy_analytics_v2_empty_state_and_public_error_sanitizer`: expected paper-copy-analytics-v2, code returns v3.
4. `test_quality_readiness_scanner_and_fusion_v3_semantics`: expected entry-readiness-v3, code returns v4.
5. `test_readiness_reports_every_failure_and_can_pass`: fixture omits new balance/risk readiness fields, so it now fails with `BALANCE_READ_FAILED` and `RISK_PROFILE_INCOMPLETE`.
6. `test_pump_and_dump_reversal_distinguish_continuation_from_exhaustion`: code returns `PUMP_CONTINUATION`, outside the older expected reversal-state set.

Interpretation: four failures are explicit version-contract drift, one is a stale readiness fixture after stricter gates, and one is a genuine semantic classification drift requiring product/strategy intent review. They should not be “fixed green” until v10.4 contracts are declared. Import smoke also found `bot` and `core.engine.pipeline` importable, while `core.pipeline` fails with `ModuleNotFoundError: core.scoring`.

Coverage is broad in count but does not prove the two P0 runtime issues: tests can validate state-machine parts while missing route-composition inconsistency and completed-market-order behavior against a realistic exchange fixture.

## Phase 1 entry criteria (recommendation only)

No changes are made here. Before feature work or major reconstruction, Phase 1 should require:

1. publish one version/release contract and make HEAD/code/config/docs/tests agree;
2. define one immutable `DecisionEnvelope`-equivalent contract without adding a new scoring engine, and make every producer call one shared orchestration function;
3. change signal admission to fail closed when the gate is absent, with route parity tests;
4. define authoritative timestamp/candle-closure semantics and validate them before analysis;
5. implement exchange-order-history reconciliation and normal ACK → fill → position progression before enabling LIVE;
6. prove protective stop/TP presence and define the TP2/TP3 policy;
7. separate labels and stores for conceptual signal statistics, simulated PAPER performance, backtest research, and exchange-fill performance;
8. create deterministic adversarial tests for gaps, wicks, same-candle entry/stop/TP, partial fills, timeouts, crash points, duplicate client IDs, and missing protection;
9. keep all LIVE flags disabled until these tests and a fresh certified demo/VST run pass.

## Audit limitations

- No runtime production database was opened; schema was audited from code to avoid destructive or credential-adjacent access.
- No external API was called and no market claims were verified against current exchange documentation.
- No real bot startup was performed because that could touch configured storage/network services; `bot` was import-smoked only.
- No live/dry-run/demo order was submitted.
- The final user requirement was truncated after `liquidatio`; this report covers liquidation behavior but cannot infer any omitted objectives.

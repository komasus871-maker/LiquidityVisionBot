# Runtime Authority Map

This document is the Phase 1A/1B runtime map for the existing Liquidity Vision codebase. It is grounded in [PHASE_0_AUDIT.md](../../PHASE_0_AUDIT.md) and describes implemented behavior, not a production-readiness claim.

## Current ownership at a glance

| Concern | Current runtime owner | Important boundary or limitation |
|---|---|---|
| Market analysis | `OKXProvider` -> `Market.get_klines` -> `DataIntegrityEngine.prepare_market_frame` -> `Analyzer` | Provider-frame truth is checked before feature stages; `MarketIntelligenceEngine` remains research-only and has no execution authority. |
| Trade decision | `Analyzer` originates LONG/SHORT/NO_TRADE analysis; `DecisionQualityEngine` alone owns semantic APPROVED/NO_TRADE admission | `ConvictionEngine`, `UnifiedDecisionEngine`, and `DecisionBrain` remain explanatory/read-model outputs and cannot approve promotion. |
| Decision veto | `TradePlanIntegrity` validates geometry inside analysis; `DecisionQualityEngine` applies the final quality/data veto; `SignalRecorder` applies only persistence, lifecycle, and portfolio constraints | Manual, scanner, watch, and observation routes now use the same versioned authority contract. |
| Planning | `TradePlanIntegrity` for deterministic entry/SL/TP geometry; `CopyExecutionPlanner` and `ExecutionValidator` for copy plans | `ExecutionValidator` rechecks persisted decision provenance before either PAPER or LIVE/copy planning. |
| Risk | `ExecutionValidator` for PAPER; `LiveCopyDispatcher`, `LiveSizer`, `LiveRiskRepository`, readiness/PnL/recovery/kill-switch gates, and `LiveExecutionCoordinator` for LIVE | Exchange-side validation is finally enforced by `BingXSwapAdapter`. |
| Execution | `CopyExecutionEngine` + `PaperExecutionLifecycle` for PAPER; `LiveExecutionCoordinator` for LIVE | PAPER and LIVE remain separate lifecycles pending later unification. |
| Exchange submission | `LiveExecutionCoordinator.submit` -> `BingXSwapAdapter.place_order` | Emergency close and VST certification use the same coordinator with distinct authority sources. |
| Reconciliation | `LiveExecutionCoordinator.recover` per execution; `LiveReconciliationService` per account; `LiveRecoveryService` at startup | Exchange evidence is authoritative; no automatic close or account re-enable. |
| Position state | `paper_execution_positions` for PAPER; exchange positions plus the fill-derived `live_positions` mirror for LIVE | A position alone cannot be attributed to a specific missing order safely. |
| PnL/statistics | `SignalHistory` for conceptual R outcomes; `PaperExecutionLifecycle`/paper analytics for simulation; `LiveDailyPnlService` for exchange-fill/position LIVE PnL | These are distinct truth domains and must not be presented as interchangeable. |

## Signal-creation routes

| Source | Analysis and decision path | Recorder / execution capability | Prior bypass status |
|---|---|---|---|
| Telegram manual analysis and refresh callbacks | `handlers.analyze._run_analysis` -> `run_analysis` -> `Analyzer`/`TradePlanIntegrity` -> `ProbabilityEngine` -> `DecisionQualityEngine` | `handlers.analyze._send_analysis` / callbacks -> `SignalRecorder` | Already gated; now carries explicit `MANUAL_ANALYZE` provenance. |
| Scanner | `Scanner.analyze_coin` -> `run_analysis` -> `Analyzer`/`TradePlanIntegrity` -> `ProbabilityEngine` -> `DecisionQualityEngine` | Informational result only; no direct recorder | Already used DecisionQuality, but now uses the complete common enrichment path and explicit `SCANNER` provenance. |
| Automated watch | `WatchEngine._analyze_one` -> `run_analysis` -> `Analyzer`/`TradePlanIntegrity` -> `ProbabilityEngine` -> `DecisionQualityEngine` | `SignalRecorder`; can feed copy after persistence | **Phase 0 bypass fixed:** it previously omitted `DecisionQualityEngine`. |
| Background observation | `ObservationMonitor.check_once` -> `run_analysis` -> `Analyzer`/`TradePlanIntegrity` -> `ProbabilityEngine` -> `DecisionQualityEngine` | `SignalRecorder`; rejected decisions remain observations | **Phase 0 bypass fixed:** it previously omitted both probability context and `DecisionQualityEngine`. |
| Alerts/watch events | Consume watch snapshots after the same gate | Informational notification/event only | Never an independent signal authority. |
| AI, market intelligence, research, EdgeDiscovery, `HistoricalReplayEngine` | Supply context, evidence, rankings, explanations, post-signal snapshots, or side-effect-free replay outcomes | No direct `SignalRecorder`/planner authority; fabricated advisory flags fail recorder/planner authorization | Replay is `HISTORICAL_REPLAY` only and cannot mix with PAPER/LIVE metrics. |
| Copy/PAPER/LIVE | `SignalHistory` row -> `CopyExecutionPlanner` -> `ExecutionValidator.validate` -> `DecisionQualityEngine.authorization` -> mode-specific execution | Execution-capable only with persisted Phase 1B approval provenance | Previously trusted an ACTIVE-shaped row without upstream decision provenance. |
| `core.database.Database.save` | Legacy `core.history_engine` / `core.statistics_engine` schema | Disconnected from production `SignalHistory` and copy execution | Remaining legacy surface; not a production bypass in the traced runtime. |

## Decision authority path

```text
OKXProvider.get_klines -> Market.get_klines cache
              |
              v
DataIntegrityEngine.prepare_market_frame [UTC, CLOSED_ONLY, OHLCV, gaps, freshness]
              |
analysis_runtime.run_analysis -> Analyzer.analyze
              |
              v
UnifiedAnalysisPipeline rechecks the same market-data contract
              |
              +-> TradePlanIntegrity (entry / stop / targets / plan_valid)
              +-> MarketIntelligenceEngine (research context only)
              +-> invalid data: explicit NO_TRADE analysis envelope; no feature stages
              |
              v
ProbabilityEngine.enrich (historical context; no approval authority)
              |
              v
DecisionQualityEngine.enrich [decision-authority-v1]
              |
       +------+------+
       |             |
   APPROVED       NO_TRADE
       |             |
       v             v
SignalRecorder   ObservationHistory only
       |
       v
SignalHistory.features_json (source/path/version/outcome/veto/timestamp)
       |
       v
CopyExecutionPlanner -> ExecutionValidator.validate -> DecisionQualityEngine.authorization
       |
       +-> PAPER: CopyExecutionEngine / PaperExecutionLifecycle
       +-> LIVE/COPY: approved journal -> LiveCopyDispatcher -> LiveExecutionCoordinator
```

`ConvictionEngine`, `UnifiedDecisionEngine`, and `DecisionBrain` are still evaluated by `DecisionQualityEngine` for reporting after semantic admission is calculated. Their output cannot turn a NO_TRADE result into APPROVED.

The detailed candle rules, states, cache behavior, and route map are in [MARKET_DATA_CONTRACT.md](MARKET_DATA_CONTRACT.md). There is one public REST candle provider (`OKXProvider`), no candle fallback, and no websocket candle decision source in the traced runtime. `Market` revalidates cached timestamps on every read; a cache hit is never freshness evidence.

Historical replay is a non-authoritative offline boundary: `HistoricalReplayEngine` accepts caller-supplied closed candles, validates their Phase 2A-compatible chronology, and produces `HISTORICAL_REPLAY` outcome artifacts only. It has no database writes, market/provider calls, `SignalRecorder`, planner, PAPER, or LIVE import path. See [RESEARCH_CLOCK_CONTRACT.md](RESEARCH_CLOCK_CONTRACT.md).

## LIVE economic entry points

Only three runtime paths can reach the adapter's economic `place_order` method, and all three pass through `LiveExecutionCoordinator.submit`:

1. `LiveCopyDispatcher.process_claimed` submits a sealed, deterministic approved-plan request.
2. `LiveEmergencyCloseService.execute` submits an explicitly confirmed reduce-only close.
3. `BingXCertificationService.economic_test` submits an entry and reduce-only close in the opt-in VST certification environment.

No handler, strategy, AI service, or reconciliation worker calls `place_order` directly. The coordinator is the sole write boundary for exchange order creation. `LiveExecutionCoordinator.cancel` is the corresponding cancellation boundary.

```text
signal / emergency confirmation / VST certification
                       |
                       v
        immutable intent + deterministic client ID
                       |
                       v
          LiveExecutionCoordinator.submit
                       |
          durable SUBMITTING + attempt row
                       |
                       v
             ExchangeAdapter.place_order
                       |
              SUBMITTED response
                       |
                       v
 order ID -> client ID -> open orders -> fills -> positions
                       |
                       v
 ACKNOWLEDGED / PARTIALLY_FILLED / FILLED / CANCELLED /
 REJECTED / UNKNOWN / RECOVERY_REQUIRED
```

## Authority by fact

| Fact | Runtime authority | Durable representation |
|---|---|---|
| Economic intent | Sealed approved plan, confirmed emergency request, or explicit VST certification request | `live_order_intents`, `live_executions` |
| Idempotency | `execution_key`, deterministic `client_order_id`, DB uniqueness, compare-and-set transitions | `live_order_intents`, `live_executions`, `live_execution_attempts` |
| Whether an order exists | Exchange order lookup by exchange ID or client ID; open orders are only supporting evidence | `exchange_order_id`, state, audit events |
| Terminal order status | Direct exchange order truth, corroborated by fills where applicable | `live_executions.state` |
| Executed quantity, price, fees | Provider fill IDs; exchange order summary is a temporary aggregate fallback when detailed fills lag | `live_execution_fills`, execution aggregates |
| Position quantity | Fill-derived execution ledger, compared with exchange positions | `live_positions` |
| Permission to submit | Existing environment, feature, account, risk, readiness, certification, recovery, and kill-switch gates | account/risk/readiness/recovery tables |

## Recovery and reconciliation authority

`LiveExecutionCoordinator.recover` owns per-execution reconstruction. Its lookup order is:

1. direct query by persisted exchange order ID;
2. direct query by deterministic client order ID;
3. open-order collection lookup;
4. fill history, filtered by order ID or client order ID;
5. exchange positions as account-level corroboration.

The current adapter protocol has no separate closed-orders collection. BingX's direct order lookup is therefore the available closed/terminal-order source; fill history supplies independent completed-execution evidence.

`LiveReconciliationService` owns account-level comparison. It invokes direct per-execution recovery for every in-flight or ambiguous local execution before obtaining account-wide open-order and position comparison snapshots, re-reads the durable ledger, and then reports only mismatches that remain. Thus an open-orders endpoint failure cannot prevent a direct order/fill recovery attempt, while account-wide provider failure still leaves LIVE fail-closed. It never treats absence from open orders alone as proof of loss.

Startup recovery (`LiveRecoveryService`) uses the same reconciliation path. New LIVE entries remain blocked while `SUBMITTING`, `SUBMITTED`, `UNKNOWN`, `RECONCILING`, or `RECOVERY_REQUIRED` records remain unresolved. The periodic worker retains the existing fail-closed response to account-wide provider unavailability.

## Execution invariants

1. Every LIVE economic call has a durable immutable intent before the adapter is called.
2. One `execution_key` maps to one intent checksum and one deterministic client order identity.
3. An ambiguous submission is never retried as a fresh order; recovery performs read-only lookup.
4. Exchange evidence is authoritative for order existence, order status, fills, fees, and positions.
5. Absence from the open-order list is never sufficient evidence that an order failed or vanished.
6. `ACKNOWLEDGED` means an exchange-identified, recognized open order with no confirmed fill quantity.
7. `PARTIALLY_FILLED` and `FILLED` require exchange-confirmed executed quantity.
8. `REJECTED` and `CANCELLED` remain distinct terminal outcomes; `FAILED` is reserved for a proven non-ambiguous failure.
9. Lost responses and contradictory evidence remain `UNKNOWN` or `RECOVERY_REQUIRED`, never optimistic success or failure.
10. Fill ingestion is idempotent by account and exchange fill ID; replay cannot increase quantity or fees.
11. Local position quantity is derived from exchange-confirmed executions and is checked against exchange position truth.
12. Restart and periodic reconciliation use the same state machine and cannot place an order, bypass safety gates, or auto-close a position.
13. Every submission failure or ambiguity remains durable in the execution, attempt, and audit records; it cannot silently disappear.
14. Requested quantity/reference price remain distinct from executed quantity/average fill/commission, so downstream statistics cannot treat an intent as a confirmed fill.

## Lifecycle actually implemented

```text
CREATED -> VALIDATED -> QUEUED -> SUBMITTING -> SUBMITTED
                                             |      |
               timeout / ambiguity ----------+      +-> RECONCILING
                       |                                  |
                       v                                  +-> ACKNOWLEDGED
                    UNKNOWN <-----------------------------+-> PARTIALLY_FILLED
                       |                                  +-> FILLED
                       +-> RECONCILING                    +-> CANCELLED
                       |                                  +-> REJECTED
                       +-> RECOVERY_REQUIRED              +-> UNKNOWN

ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED
       |                |
       +-> CANCEL_PENDING -> CANCELLED
       +----------------+-> RECONCILING / UNKNOWN / RECOVERY_REQUIRED

proven retryable pre-acceptance error: SUBMITTING -> RETRY_WAIT -> SUBMITTING
proven exhausted/non-ambiguous failure: SUBMITTING or RETRY_WAIT -> FAILED
```

Terminal states are `FILLED`, `CANCELLED`, `REJECTED`, and `FAILED`. `UNKNOWN`, `RECONCILING`, and `RECOVERY_REQUIRED` are safety-blocking ambiguity states.

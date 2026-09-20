# Technical Debt Register

This register prioritizes findings from [PHASE_0_AUDIT.md](../../PHASE_0_AUDIT.md). Phase 1A fixed execution-truth items; Phase 1B fixed the production decision-authority bypasses.

## P0 — money and state integrity

| Item | Current status | Required follow-up |
|---|---|---|
| Successful POST response flattened to ACK | Addressed in Phase 1A | Validate against captured BingX VST response variants. |
| Open-orders-only reconciliation | Addressed in Phase 1A | Certify lookup behavior against exchange retention windows and not-found codes. |
| Ambiguous response could invite duplicate economic intent | Guarded by deterministic identity and read-only recovery | Add concurrency/chaos validation against a real sandbox/VST account. |
| Fill/position reconstruction correctness | Mock-verified in Phase 1A | Prove commissions, partial-fill pagination, hedge mode, and reduce-only closes in VST. |
| Provider not-found semantics | Conservatively remain unresolved | Classify BingX terminal not-found versus transient/retention-window behavior. |

## P1 — decisions and performance

| Item | Current status | Required follow-up |
|---|---|---|
| Watch/observation bypass of final decision quality | Addressed in Phase 1B | Keep route-level authority-contract tests mandatory when adding producers. |
| `SignalRecorder` defaulted missing gate to approved | Addressed in Phase 1B; missing/malformed provenance fails closed and remains observation-only | Consider typed decision objects only if future schema evolution justifies them. |
| Copy planner trusted ACTIVE-shaped rows without decision provenance | Addressed in Phase 1B; `ExecutionValidator` revalidates the persisted marker | Historical rows without provable Phase 1B provenance are intentionally not copyable; re-analysis is required. |
| Multiple advisory decision representations | Authority clarified, not merged | Reconcile presentation wording without granting `ConvictionEngine`, `UnifiedDecisionEngine`, or `DecisionBrain` admission authority. |
| Versioned decision contracts drift from tests | Six baseline failures remain | Reconcile v10.4 implementation and published contracts in a separate change. |
| Daily PnL and risk source completeness | Existing fail-closed checks preserved | Validate pagination, day boundaries, fee assets, and unrealized-PnL freshness. |
| Reconciliation API budget | Direct recovery precedes account snapshots; unresolved records may require repeated bounded lookups | Add per-cycle memoization, bounded pagination, rate-budget metrics, and backoff based on certified limits. |
| Multiple lifecycle representations | Coordinator and queue states remain separate | Define one explicit mapping contract and validate every terminal state. |
| Candle-provider redundancy | One public OKX REST candle provider; provider failures now fail closed | Add a certified fallback only with explicit source provenance, parity checks, and failure-mode tests. |
| Direct research-frame timing provenance | Structural validation is shared, but arbitrary historical frames are timing-unverified by design | Introduce an explicit backtest/research clock contract before any research output is compared with runtime freshness. |
| Fixed 220-candle unified analysis window | Explicitly enforced for the existing pipeline | Derive and version per-consumer history requirements if future analysis stages legitimately need different windows. |
| Historical outcome comparability | New replay ledger is mode-segregated; existing conceptual/PAPER research rows are not replay-certified | Export immutable decision-time candles and actual/PAPER execution evidence before comparing any historical cohorts. |
| Historical microstructure/funding truth | Replay marks funding `NOT_MODELED` without an explicit supplied model | Retain venue/time-aligned funding and bid/ask data before making exchange-net or intrabar-order claims. |
| Research model selection risk | Chronological split/walk-forward primitives exist; no variant registry is populated yet | Require a predeclared hypothesis/config/variant-count manifest for Phase 3 studies. |

## P2 — reliability and architecture

| Item | Current status | Required follow-up |
|---|---|---|
| No websocket/private-stream execution updates | Polling remains authoritative | Add stream ingestion only as an optimization; retain polling recovery authority. |
| Reconciliation audit growth | Mismatch rows are now identity-idempotent | Add retention policy for successful audit events and metrics cardinality limits. |
| Summary-fill fallback shares fill table | Safely replaced by complete detailed fills | Add explicit evidence-source/provenance column in a migration if retained long term. |
| Worker suspends on provider-wide failure | Existing fail-closed policy preserved | Define operator-approved recovery/un-suspend runbook and outage grace policy. |
| Cross-process race/chaos proof | CAS and uniqueness exist | Test DB isolation levels, crashes at each transition, and multi-worker contention. |
| Legacy `core.database.Database.save` signal-shaped schema | Disconnected from the production `SignalHistory`/copy path | Retire or rename it so future callers cannot mistake it for executable signal persistence. |
| Decision provenance stored in `features_json` | Backward-compatible and sufficient for Phase 1B | Consider explicit indexed columns only if operational querying or multiple decision versions require them. |

## P3 — product and maintenance

| Item | Current status | Required follow-up |
|---|---|---|
| README/release/version drift | Not in Phase 1A scope | Align README, changelog, release name, and contract versions. |
| Legacy documentation volume | Historical files retained | Establish canonical-doc index and archive policy. |
| Operator visibility for unresolved executions | Audit/diagnostic counts exist | Add a localized read-only operator view without introducing control shortcuts. |
| Test fixture duplication | Existing pattern retained | Introduce shared adapter factories after lifecycle behavior stabilizes. |

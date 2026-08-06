# v9.9.9 — Provenance Migration and Unified Read Cutover

## Scope

v9.9.9 catalogs historical `paper_positions` into `historical_execution_records`. It does not convert incomplete history into live unified economic state. Existing unified orders, fills, positions, lifecycle events, and portfolio ledger entries remain authoritative for accounting and execution.

## Classification

- `FULLY_RECONSTRUCTABLE`: exactly one existing unified position matches the user, signal, normalized symbol, side, and terminal/open lifecycle identity. The catalog links it and analytics exclude the legacy duplicate.
- `PARTIALLY_RECONSTRUCTABLE`: factual legacy position/outcome fields exist, but fills, commissions, or lifecycle evidence are unavailable.
- `LEGACY_ONLY`: the record is useful only in its original historical semantics, including rejected attempts that are not positions.
- `AMBIGUOUS`: identity cannot be resolved safely, such as a missing source signal or multiple unified matches.
- `INVALID`: structurally impossible or unsupported status/side/quantity data.

No classification invents missing values. Provenance explicitly marks recorded, unknown, and unavailable fields.

## Idempotency and restart behavior

Each source row has stable key `paper_positions:<id>` and a deterministic SHA-256 checksum over classification, linkage, and coverage. Replays skip unchanged rows and update changed factual rows. Startup processes a bounded batch controlled by `HISTORICAL_MIGRATION_BATCH_SIZE`; durable completed-run cursors advance through the table and wrap after a complete pass so changed history is eventually reverified.

Each batch runs transactionally. A crash rolls back the batch without partially cataloging rows or creating economic state.

## Read cutover

`ExecutionRepository.closed_outcomes()` is the normalized analytics API:

1. authoritative unified closed positions;
2. unlinked partially reconstructable catalog outcomes with durable realized R;
3. explicit `LEGACY_COMPAT` rows not yet cataloged.

Linked legacy projections, unresolved records, rejected attempts, and invalid records are excluded from training outcomes. This prevents unified/legacy double counting while preserving analytics during incremental migration.

## Operations and rollback

`/migration_status` is admin-only and reports the latest run, cursor, unresolved count, and classification totals. Runtime diagnostics expose migrated and unresolved totals.

Rollback consists of deploying v9.9.8 code. The new tables are additive and ignored by v9.9.8; legacy tables are unchanged. `PORTFOLIO_ACCOUNTING_SOURCE=LEGACY` remains the accounting admission rollback switch.

## Security and live execution

No credentials or secrets are added. LIVE remains fail-closed. This release adds no exchange submission path and no AI authority over execution.

## Known limitations and next release

Legacy-only and ambiguous rows remain outside normalized training. Historical commission accuracy cannot be recovered without primary evidence. Existing actual-execution training can still include manual or panic closes; signal-quality and intervention analytics remain explicitly separate work for a later evaluation release. v9.9.10 should build the durable live execution foundation: normalized adapter capabilities, client-order-id recovery, unknown-state handling, and a formal readiness gate—without enabling LIVE by default.

# LiquidityVisionBot v9.9.5c — Journal State Machine Integration

## Scope

This release integrates lifecycle enforcement into the existing persistent copy execution journal. It does not replace the engine, adapter contract, execution planner, or database schema.

## Lifecycle

The persisted journal now permits only these state changes:

- `PLANNED -> EXECUTING | FAILED | CANCELLED`
- `EXECUTING -> EXECUTED | FAILED | CANCELLED`
- terminal states (`REJECTED`, `EXECUTED`, `FAILED`, `CANCELLED`) cannot transition to another state
- same-state writes are idempotent and preserve existing metadata unless replacements are explicitly supplied

`PLANNED -> FAILED` remains intentionally valid because LIVE mode is fail-closed before adapter claiming.

## Persistence guarantees

Transitions are validated inside `CopyExecutionJournal`, the persistence boundary shared by all callers. Updates use a compare-and-set condition on the current status so a stale caller cannot silently overwrite a concurrent transition.

## Compatibility

- Existing journal status strings are unchanged.
- Existing database tables require no migration.
- `CopyExecutionEngine`, `PaperExecutionAdapter`, and planner contracts remain compatible.
- LIVE execution remains disabled.

## Verification

The complete test suite passes: `167 passed`.

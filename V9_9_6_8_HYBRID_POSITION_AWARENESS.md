# v9.9.6.8 — Hybrid Position Awareness

This release connects real unified execution positions to runtime portfolio identity.

## Runtime behavior

- Open-position count is hybrid: confirmed legacy positions plus unmatched unified positions.
- Deduplication uses an exact shared `signal_id`; rows without a reliable correlation remain separate.
- `symbol_is_open` is true when either a confirmed legacy position or an open unified position matches the normalized symbol.
- Existing `MAX_POSITIONS` and `SYMBOL_ALREADY_OPEN` rejection codes are reused.
- Heat remains confirmed legacy heat from Safe Heat Reconciliation.
- Unified exposure and PnL are diagnostics only and do not affect equity, balance, sizing, or heat.

## Database

No schema or migration changes. No synthetic lifecycle records are created.

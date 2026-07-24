# v9.9.6.7 — Safe Heat Reconciliation

This release separates confirmed legacy portfolio risk from unresolved legacy state.

- Terminal legacy positions are still closed idempotently when their source signal is terminal.
- ACTIVE, TP1, and TP2 legacy positions are classified as confirmed active and continue to contribute to portfolio heat.
- Missing or unknown signal state is classified as unresolved and remains fail-closed.
- Unresolved state now rejects execution with `PORTFOLIO_STATE_UNRESOLVED` instead of presenting a misleading `MAX_HEAT` rejection.
- `/copy_stats` and `/positions` expose confirmed heat, unresolved heat, heat source, mismatch, and reconciliation status.
- No synthetic orders, fills, prices, or unified positions are created.

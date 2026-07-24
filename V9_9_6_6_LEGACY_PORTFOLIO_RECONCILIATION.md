# v9.9.6.6 — Legacy Portfolio Reconciliation

This release adds a conservative compatibility bridge between legacy `paper_positions` and the unified execution lifecycle.

- Terminal legacy positions whose source signal is already terminal are closed idempotently before copy statistics and risk heat are calculated.
- No synthetic orders or fills are created.
- Active legacy positions remain included in risk checks (fail-closed).
- `/copy_stats` and empty `/positions` output expose legacy/unified counts and reconciliation status.

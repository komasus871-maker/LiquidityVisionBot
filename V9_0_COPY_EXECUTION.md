# LiquidityVisionBot v9.0.0 — Data Integrity & Paper Copy Execution

## Delivered

- Multi-user `/copy` command center with persistent risk profiles.
- Paper execution worker synchronized with the existing signal lifecycle.
- Fail-closed execution validation: active status, plan geometry, entry deviation, timestamps, position count and portfolio heat.
- Stop-distance-based position sizing using percentage account risk.
- Persistent `copy_profiles`, `paper_positions`, and `execution_events` tables for SQLite and PostgreSQL.
- Paper position lifecycle for ACTIVE, TP1, TP2 and terminal signal states.
- `/copy_enable`, `/copy_disable`, `/copy_risk`, `/copy_balance`, `/copy_limits`, `/copy_stats`, and `/panic`.
- LIVE mode is intentionally hard-disabled until exchange reconciliation and credential storage are implemented.

## Deployment

Database migrations are applied automatically by `create_tables()` on startup. No manual SQL is required.

Recommended environment:

- `COPY_EXECUTION_INTERVAL=60`
- existing PostgreSQL `DATABASE_URL`

## Safety boundary

v9.0 does not accept exchange API keys and cannot submit real orders. It provides the production-shaped paper layer needed to validate execution behavior before live connectivity.

# LiquidityVisionBot v9.9.10 — Durable Live Execution Foundation

This release adds the persistence and safety boundary required for future real-order adapters. It does not enable real-money trading by default. Existing paper execution remains authoritative and unchanged.

## Modes and safety

- `PAPER` is the default and continues to use the existing simulated lifecycle.
- `SHADOW` plans and validates without exchange interaction.
- `LIVE_DRY_RUN` may read account and symbol truth, but the coordinator never calls `place_order`.
- `LIVE` is fail-closed unless the environment flag, account enablement, two-step confirmation, credential reference, adapter capabilities, synchronization, portfolio reconciliation, limits, daily-loss guard, kill switch and emergency-close capability all pass.
- `DISABLED` performs no execution.

Secrets remain in the existing encrypted credential store; live account rows contain only opaque references. Normalized errors redact key, secret, signature, passphrase and token values. Withdrawal permission is never required and should remain disabled.

## Deployment

Set `APP_VERSION=9.9.10`, `EXECUTION_MODE=PAPER`, `LIVE_EXECUTION_ENABLED=false`, `ENVIRONMENT=production`, a persistent `DATABASE_URL`, and `EXCHANGE_CREDENTIALS_MASTER_KEY`. Deploy once to apply additive tables. Verify `/live_readiness`, `/recovery`, `/copy_stats`, `/runtime`, and paper execution before considering `LIVE_DRY_RUN`.

Do not set `EXECUTION_MODE=LIVE` for this release unless a production adapter implements every required normalized capability and the account has separately passed operational approval. There is intentionally no Telegram command that releases the kill switch or sets `live_enabled`.

## Rollback

Set `LIVE_EXECUTION_ENABLED=false` and `EXECUTION_MODE=PAPER` (or `DISABLED`) before rolling application code back to v9.9.9. The migration is additive; v9.9.9 ignores the new tables. Retain them for audit and recovery. Never delete executions in `UNKNOWN` or `RECOVERY_REQUIRED`; establish exchange truth first.

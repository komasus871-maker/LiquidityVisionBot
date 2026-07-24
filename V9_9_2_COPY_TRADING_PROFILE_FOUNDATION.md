# v9.9.2 — Copy Trading Profile Foundation

This release extends the existing `copy_profiles` model instead of introducing a parallel copy-trading subsystem.

## Added

- Unified position sizing mode: `RISK_PERCENT` or `FIXED_USDT`.
- Per-user fixed USDT size, leverage, maximum positions, and Auto Copy preference.
- Central profile validation with fail-closed limits.
- Fixed-USDT sizing through the existing `ExecutionValidator`, preserving portfolio, confidence, slippage, cooldown, heat, daily-loss, and notional guardrails.
- Telegram commands: `/copy_size`, `/copy_leverage`, and `/copy_auto`.
- Forward-compatible schema migration columns and regression coverage.

## Safety boundary

`auto_copy` is a persisted preference and integration contract for the next executor release. It does not bypass the existing demo/testnet requirement and does not enable LIVE order execution. LIVE remains fail-closed.

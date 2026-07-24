# v9.9.6.9 — Unified Risk-Aware Portfolio

This release makes real unified paper positions participate safely in portfolio
heat without double-counting the legacy compatibility lifecycle.

## Runtime behavior

- New unified orders durably retain the planned stop and risk amount.
- Unified positions retain initial quantity and initial risk amount.
- Remaining unified heat is the open quantity as a fraction of initial quantity.
- Exact legacy/unified matches by `signal_id` use legacy heat once.
- Pre-release unified positions with missing durable risk metadata remain
  diagnostics-only and do not contribute guessed heat.
- Unified realized and unrealized PnL remain diagnostic and do not change paper
  balance, equity, sizing, cooldown, or daily-loss accounting.

## Safety

- LIVE execution remains disabled.
- No synthetic orders, fills, prices, stops, or positions are created.
- Existing databases receive nullable additive columns through the current
  idempotent schema bootstrap.

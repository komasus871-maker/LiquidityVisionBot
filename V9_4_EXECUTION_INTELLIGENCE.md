# v9.4.0 — Execution Intelligence & Rejection Analytics

The paper executor now exposes its complete decision funnel instead of only a cumulative rejection counter.

## Added

- Read-only rejection analytics service with deterministic aggregation.
- 30-day accepted/rejected execution funnel and acceptance rate.
- Ranked rejection reasons, symbols, and timeframes.
- Recent rejected-attempt diagnostics through `/copy_rejections`.
- Dominant rejection reason in `/copy` and `/copy_stats`.
- Fail-closed guarantee: analytics never relax risk guardrails automatically.

## Operational purpose

This release makes it possible to distinguish healthy guardrail activity from configuration problems, weak signal quality, concentration limits, cooldown pressure, and adaptive-policy blocking without inspecting the database manually.

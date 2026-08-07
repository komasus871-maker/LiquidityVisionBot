# LiquidityVisionBot v9.9.15 — OpenAI Provider Certification Readiness

## Release invariants

AI remains advisory-only. It cannot admit a trade, size an order, mutate a position, call an exchange, or change portfolio accounting. `AI_GATED` is mapped to `AI_OFF`. A provider error, full observation queue, invalid configuration, expired certificate, kill switch, or governance block affects only AI observation.

Both transports now follow one path: provider envelope → protocol extraction → one JSON parse → Draft 2020-12 schema checks → domain checks → market-truth checks → semantic checks → normalized decision. Chat validates `choices[0].message.content`. Responses searches backward for the final assistant message and ignores reasoning items. Raw provider bodies, output text, refusals, and reasoning content are not persisted or shown in Telegram; only checksums and normalized metadata are retained.

## Recommended OpenAI observation identity

Use the exact variables in `OPENAI_PROVIDER_RUNBOOK.md`. The recommended protocol is Responses at `https://api.openai.com/v1/responses`, model `gpt-5.6-terra`, strict JSON Schema, `AI_REASONING_EFFORT=low`, no JSON-object fallback, one attempt, concurrency one, and a bounded observation queue. Pricing remains external and versioned. The release example verified for 2026-08-07 is $2.50/M input, $0.25/M cached input, $3.125/M cache writes, and $15.00/M output; re-verify pricing before production activation. Usage normalization persists cache reads, cache writes, and reasoning tokens independently without retaining provider content.

## Certification

`/ai_certification run` is an intentional paid operation. Telegram warns before the call. It creates a durable `RUNNING` row, acquires a short per-identity claim, makes exactly one bounded request using an immutable synthetic context and at most `AI_CERTIFICATION_MAX_TOKENS`, then persists completion, latency, request ID, usage, cost status, validation stage/code, and expiry. There is no repair call and no certification retry. Repeats inside the suppression window return `CERTIFICATION_DUPLICATE_SUPPRESSED` without another provider call.

Certification states are `NOT_CONFIGURED`, `CONFIG_INVALID`, `MISSING`, `RUNNING`, `PASSED`, `FAILED`, `EXPIRED`, `IDENTITY_CHANGED`, and `SUSPENDED`. A change to any identity field creates a different checksum and cannot reuse the old certificate.

## Governance and observation

Promotion evidence is limited to decisions whose `provider_identity_checksum` exactly matches the active certificate. Legacy rows remain untouched. Disabled, unscoped, different-provider, different-protocol, different-endpoint, different-model, different-model-version, different-prompt, different-schema/checksum, different-context/request format, different-pricing, different-output-mode, different-reasoning, and different-capability decisions are excluded.

Observation concurrency and candidate depth are bounded. Snapshot claims survive process failure until expiry; duplicates and queue overflow are recorded in `ai_observation_events`. Deterministic trading does not wait for or depend on the observation queue.

Promotion to `SHADOW_CERTIFIED` requires a current certificate, kill switch off, valid configuration/pricing, the configured exact-identity sample minimum, schema/semantic rates, bounded transport failure rate, and no high-severity drift. Profitability is not required for shadow because shadow has no trading authority. No automatic promotion occurs.

## Rollback

Run `/ai_kill on`, set `AI_TRADING_MODE=AI_OFF`, and set `AI_PROVIDER=disabled`, then redeploy. The additive tables and columns may remain. Do not roll back database columns or rewrite historical AI decisions.

# LiquidityVisionBot v9.9.14 — AI Provider Certification and Shadow Evaluation

> Historical release document. v9.9.15 supersedes the provider parsing, certification-state, identity scope, pricing example, and rollout procedure. Use `OPENAI_PROVIDER_RUNBOOK.md` for production.

## Production activation

AI is advisory-only. Start with `AI_PROVIDER=disabled` and `AI_TRADING_MODE=AI_OFF`, deploy the additive migration, then configure the provider. A non-disabled production HTTP provider cannot be invoked until its exact identity has a passing, unexpired certification. Identity includes provider, protocol, redacted full endpoint, model and model version, prompt/schema/context/request versions, pricing version and declared capabilities.

Recommended OpenAI Responses configuration:

```env
AI_PROVIDER=openai
AI_PROVIDER_PROTOCOL=responses
AI_PROVIDER_ENDPOINT=https://api.openai.com/v1/responses
AI_PROVIDER_API_KEY=<Render secret; never paste into Telegram or logs>
AI_MODEL=<approved model snapshot>
AI_MODEL_VERSION=<same approved snapshot>
AI_STRUCTURED_OUTPUT_MODE=json_schema
AI_STRICT_SCHEMA_REQUIRED=true
AI_SUPPORTS_JSON_SCHEMA=true
AI_SUPPORTS_STRICT_SCHEMA=true
AI_SUPPORTS_USAGE_REPORTING=true
AI_SUPPORTS_REQUEST_ID=true
AI_PRICE_VERSION=<dated pricing source>
AI_INPUT_COST_PER_MILLION_USD=<current exact rate>
AI_OUTPUT_COST_PER_MILLION_USD=<current exact rate>
AI_CACHED_INPUT_COST_PER_MILLION_USD=<current exact rate>
AI_CERTIFICATION_TTL_HOURS=24
AI_TRADING_MODE=AI_OBSERVE
AI_MAX_CONCURRENCY=1
AI_MAX_DAILY_REQUESTS=25
AI_MAX_DAILY_COST_USD=1
```

The endpoint must include the complete protocol path. Secrets are used only in the bearer Authorization header and are excluded from identity, persistence, Telegram, request checksums and logs.

## Certification and rollout

1. Keep the durable kill switch on with `/ai_kill on` while entering configuration.
2. Inspect `/ai_provider`. Configuration must be `VALID`.
3. Run `/ai_certification run` as an administrator. Certification sends a synthetic advisory-only structured request and persists the result and expiry. Failure leaves the provider suspended.
4. Turn the switch off with `/ai_kill off`; keep `AI_OBSERVE` until 24-hour telemetry is stable.
5. Inspect `/ai_status`, `/ai_metrics`, `/ai_cost`, `/ai_drift`, and `/ai_experiments`. Do not claim quality improvement for insufficient cohorts.
6. After sufficient clean observation evidence, run `/ai_certification promote shadow`, complete the operational review, and then move to `AI_SHADOW`. Promotion is fail-closed below 30 requests or below the schema/semantic thresholds. Assist promotion requires at least 100 resolved calibration samples. `AI_GATED` is blocked and must not be configured.

Changing any identity field invalidates the previous certification automatically because the checksum changes. Certification expiry also blocks activation.

## Governance and safety

Governance states are `UNVERIFIED`, `OBSERVING`, `SHADOW_CERTIFIED`, `ASSIST_CERTIFIED`, `SUSPENDED`, and `RETIRED`. v9.9.14 certification enters `OBSERVING`; `/ai_certification promote shadow|assist` requires evidence and an administrator. `/ai_certification suspend|retire` is immediate. No automatic promotion occurs.

`/ai_kill on` is global, database-backed, immediate at the request boundary and restart-persistent. It does not alter deterministic signals, copy decisions, order sizing, exchange calls, positions, stops, closes or accounting. Rollback is `/ai_kill on`, `AI_PROVIDER=disabled`, and `AI_TRADING_MODE=AI_OFF`; additive audit tables may remain safely.

## Evaluation semantics

Signal outcome, deterministic policy outcome, execution result, operator intervention and counterfactual result are stored separately. Manual intervention is excluded from calibration evidence. Rolling telemetry covers request and validation success, abstention, timeout, rate limit and server errors, p50/p95/p99 latency, tokens, cost, provider request IDs and downgrades. Drift and counterfactual reports fail closed to `INSUFFICIENT_SAMPLES` below their threshold.

Cost remains `UNPRICED` unless an explicit pricing version and input/output rates exist. Provider invoices can be entered into the additive reconciliation ledger; variance is never silently treated as zero.

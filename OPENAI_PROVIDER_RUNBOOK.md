# OpenAI provider production-observation runbook

## Render variables

Keep `AI_PROVIDER=disabled` and `AI_TRADING_MODE=AI_OFF` for the migration deployment. Add `AI_PROVIDER_API_KEY` only as a Render secret.

```env
APP_VERSION=9.9.18
AI_TRADING_MODE=AI_OFF
AI_PROVIDER=openai
AI_PROVIDER_PROTOCOL=responses
AI_PROVIDER_ENDPOINT=https://api.openai.com/v1/responses
AI_PROVIDER_API_KEY=<Render secret>
AI_MODEL=gpt-5.6-terra
AI_MODEL_VERSION=gpt-5.6-terra
AI_STRUCTURED_OUTPUT_MODE=json_schema
AI_STRICT_SCHEMA_REQUIRED=true
AI_ALLOW_JSON_OBJECT_FALLBACK=false
AI_SCHEMA_VERSION=ai-decision-v3
AI_REASONING_EFFORT=low
AI_SUPPORTS_JSON_OBJECT=false
AI_SUPPORTS_JSON_SCHEMA=true
AI_SUPPORTS_STRICT_SCHEMA=true
AI_SUPPORTS_TEMPERATURE=false
AI_SUPPORTS_MAX_TOKENS=false
AI_SUPPORTS_MAX_COMPLETION_TOKENS=false
AI_SUPPORTS_MAX_OUTPUT_TOKENS=true
AI_SUPPORTS_USAGE_REPORTING=true
AI_SUPPORTS_REQUEST_ID=true
AI_SUPPORTS_REASONING_MODELS=true
AI_SUPPORTS_RETRYABLE_IDEMPOTENT_REQUESTS=false
AI_PRICE_VERSION=openai-gpt-5.6-terra-standard-2026-08-07
AI_INPUT_COST_PER_MILLION_USD=2.50
AI_CACHED_INPUT_COST_PER_MILLION_USD=0.25
AI_CACHE_WRITE_COST_PER_MILLION_USD=3.125
AI_OUTPUT_COST_PER_MILLION_USD=15.00
AI_REQUIRE_PRICING_FOR_REQUESTS=true
AI_REQUEST_TIMEOUT_SECONDS=45
AI_PROVIDER_MAX_RESPONSE_BYTES=1000000
AI_PROVIDER_MAX_ATTEMPTS=1
AI_MAX_TOKENS=1200
AI_MAX_CONCURRENCY=1
AI_OBSERVE_QUEUE_DEPTH=10
AI_OBSERVE_DROP_AUDIT_LIMIT=25
AI_SHADOW_INTERVAL=60
AI_CIRCUIT_BREAKER_FAILURES=5
AI_CIRCUIT_BREAKER_SECONDS=300
AI_OBSERVATION_CACHE_ENABLED=true
AI_OBSERVATION_CACHE_TTL_SECONDS=180
AI_MAX_DAILY_REQUESTS=100
AI_MAX_DAILY_REQUESTS_PER_USER=10
AI_MAX_DAILY_COST_USD=2
AI_CERTIFICATION_MAX_TOKENS=1200
AI_CERTIFICATION_TTL_HOURS=24
AI_CERTIFICATION_REPEAT_SUPPRESSION_SECONDS=60
AI_PROMOTION_MIN_SHADOW_DECISIONS=30
AI_PROMOTION_MIN_ASSIST_DECISIONS=100
AI_PROMOTION_MIN_SCHEMA_VALID_RATE=0.99
AI_PROMOTION_MIN_SEMANTIC_VALID_RATE=0.95
AI_PROMOTION_MAX_TRANSPORT_FAILURE_RATE=0.05
```

The endpoint includes the full Responses path. The API key is used only in the bearer `Authorization` header and is excluded from identity, persistence, checksums intended for display, Telegram, and logs. Pricing is configuration, not code; verify current OpenAI pricing before activation and change `AI_PRICE_VERSION` whenever rates change. The cache-write rate is 1.25 times the normal input rate for GPT-5.6. Cache reads and writes are normalized separately because OpenAI may report overlapping counts. A pricing-version change deliberately invalidates the previous certificate.

`ai-decision-v3` uses explicit evidence identifiers. Every supporting and conflicting item has a unique lower-snake-case `evidence_id`; supporting items also declare integer strength and conflicting items declare severity. `evidence_ranking` must reference every supporting ID exactly once with contiguous ranks, must never reference a conflicting ID, and rank 1 is strongest. Strength ties are resolved by ascending `evidence_id`. Input array order is irrelevant; the semantic validator normalizes explicit ranks. A non-empty supporting set with omitted ranking fails closed. The schema, prompt, certification and normal observation paths all enforce this same contract.

Chat Completions remains a tested fallback. Use the full `https://api.openai.com/v1/chat/completions` endpoint, `AI_PROVIDER_PROTOCOL=chat_completions`, `AI_SUPPORTS_MAX_OUTPUT_TOKENS=false`, and exactly one supported Chat token flag. For a reasoning-capable model set `AI_SUPPORTS_MAX_TOKENS=false`, `AI_SUPPORTS_MAX_COMPLETION_TOKENS=true`, and `AI_SUPPORTS_TEMPERATURE=false`. Keep strict JSON Schema enabled and JSON-object fallback disabled. Changing protocol or any capability creates a new identity and requires a new certificate.

## Safe activation and Telegram checks

1. Deploy the additive migration with AI disabled.
2. Configure the variables above but leave `AI_TRADING_MODE=AI_OFF`; deploy.
3. Run `/ai_kill on`, `/ai_provider`, and `/ai_certification`. PASS: configuration `VALID`, strict output `json_schema`, state `MISSING` or `IDENTITY_CHANGED`, and no secret in output. FAIL: any configuration error.
4. Set `AI_TRADING_MODE=AI_OBSERVE`; deploy. Provider activation remains blocked until certification.
5. Run `/ai_certification run` once. It contacts OpenAI and incurs a small real charge. PASS: `PASSED`, stage `COMPLETE`, usage/cost `PASS`, and request ID available. FAIL: any normalized failure code; correct configuration or provider output before repeating.
6. Run `/ai_kill off`, then `/ai_provider`, `/ai_status`, `/ai_metrics`, `/ai_cost`, and `/ai_drift`. PASS: certification `PASSED`, governance `OBSERVING`, current identity hash consistent, current request count grows within limits, schema/semantic metrics are healthy, and cost is `PRICED`.
7. After the configured evidence window, run `/ai_certification promote shadow`. Review any exact blockers. Only after `SHADOW_CERTIFIED`, set `AI_TRADING_MODE=AI_SHADOW` and deploy.

Any prompt, schema-version, or schema-checksum change invalidates the prior provider identity. Certification is never automatic: after deploying `ai-decision-v3`, an operator may run exactly one paid `/ai_certification run` after the local and deployment checks pass.

## Observation failure diagnostics

Certification uses a fresh synthetic context; production observations use each signal's persisted `updated_at` as immutable market time. A certified provider can therefore pass certification while an old active signal fails `MARKET_TRUTH_VALIDATION / STALE_CONTEXT`. Stale/future/invalid persisted timestamps are rejected before a paid observation call and do not count against the provider circuit. They remain fail-closed advisory ABSTAIN rows with quality zero.

`/ai_provider_health` separates provider transport failures, provider-response validation failures, local observation validation failures, valid versus fallback abstentions, provider latency, semaphore wait, end-to-end latency, and exact validation code/stage histograms. The existing schema-pipeline percentage retains its governance definition; structural schema valid/invalid/not-evaluable counts explain whether a fall came from malformed JSON Schema output or from requests such as timeouts where no schema was available. No prompt, schema semantics, token limits, retry policy, trading threshold, or execution authority is changed by these diagnostics.

Historical circuit counters are not silently reset. If an older deployment counted local context failures as provider failures, `/ai_provider_health` reports the classification drift. The durable circuit remains fail-closed until its existing cooldown expires and the normal single half-open probe receives a provider result; future local freshness rejections do not increment it.

Do not enable `AI_GATED`. `/ai_kill on` plus `AI_PROVIDER=disabled` and `AI_TRADING_MODE=AI_OFF` is the immediate rollback.

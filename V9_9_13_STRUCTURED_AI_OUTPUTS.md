# LiquidityVisionBot v9.9.13 — Structured AI Outputs

## Protocols

`AI_PROVIDER_PROTOCOL=chat_completions` sends `messages` and either Chat Completions `response_format.json_schema` or legacy `json_object`. `AI_PROVIDER_PROTOCOL=responses` sends `instructions`, `input`, `max_output_tokens`, and `text.format.json_schema`. Both normalize output, usage, request ID, model identity and pricing metadata into the same immutable decision contract.

The endpoint is always a complete URL. For OpenAI use `https://api.openai.com/v1/chat/completions` or `https://api.openai.com/v1/responses` to match the configured protocol.

## Capability and fallback contract

Capabilities are explicit environment-backed values and appear in diagnostics. `AI_STRUCTURED_OUTPUT_MODE=auto` selects strict JSON Schema when both schema and strict support are declared. If strict output is unavailable, JSON-object fallback occurs only when `AI_ALLOW_JSON_OBJECT_FALLBACK=true`; the downgrade reason is persisted. `AI_STRICT_SCHEMA_REQUIRED=true` turns missing strict support into an advisory ABSTAIN. No downgrade is silent.

The hierarchy is strict JSON Schema, recorded JSON object, then disabled/ABSTAIN. `AI_GATED` remains mapped to `AI_OFF`.

## Versioning and validation

Decisions bind prompt `ai-shadow-v2-structured`, context `ai-context-v1`, schema `ai-decision-v1`, provider request `ai-provider-request-v2`, their checksums, protocol and requested/effective output modes. Old v9.9.12 rows remain unchanged and display as legacy metadata.

Validation stages are provider transport/shape, JSON parsing, JSON Schema, domain, market truth and semantic consistency. Raw response bodies are neither logged nor displayed. Schema or semantic failures are recorded ABSTAIN decisions and are never retried.

## Pricing

Set `AI_PRICE_VERSION`, `AI_INPUT_COST_PER_MILLION_USD`, `AI_OUTPUT_COST_PER_MILLION_USD`, and optionally `AI_CACHED_INPUT_COST_PER_MILLION_USD` for the exact model snapshot. If any required price identity is absent, cost is `UNPRICED`, not zero. With `AI_REQUIRE_PRICING_FOR_REQUESTS=true`, provider invocation is blocked before cost can escape the daily ceiling.

## Safe rollout

Deploy first with `AI_PROVIDER=disabled`. Then configure an exact protocol, endpoint, model snapshot, capability flags and prices in `AI_OBSERVE` with concurrency one and small request/cost ceilings. Confirm strict effective mode, schema/semantic validity, invoice reconciliation, p95 latency and no downgrades. Move selected users to `AI_SHADOW` only after stable observation. Do not enable `AI_GATED`.

## Rollback

Set `AI_PROVIDER=disabled` or `AI_TRADING_MODE=AI_OFF` and restart. The additive v9.9.13 columns remain audit-safe and are ignored by earlier code. No execution state changes are required.

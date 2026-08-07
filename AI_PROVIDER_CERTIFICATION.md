# AI provider certification

Certification is a bounded paid health and contract test, not a trading simulation. It uses one immutable synthetic context, one provider attempt, strict structured output, and the same extraction/schema/domain/market/semantic validator as ordinary AI analysis. It never reads or changes planner, risk, sizing, position, lifecycle, portfolio, or exchange execution state.

The durable record includes identity, start/completion time, status, expiry, protocol/output/schema checks, normalized validation stage/code, request ID, latency, input/output/cache-read/cache-write/reasoning token usage, cost, pricing status, and a capability snapshot. A short durable claim suppresses concurrent or accidental repeats. Raw response content and reasoning are not persisted.

Use `/ai_certification` to inspect and `/ai_certification run` for the paid probe. See `OPENAI_PROVIDER_RUNBOOK.md` for rollout and PASS/FAIL criteria.

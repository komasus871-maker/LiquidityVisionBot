# LiquidityVisionBot v9.9.12 — GPT Trading Intelligence Shadow Layer

## Safety boundary

The AI subsystem is advisory. It imports neither exchange adapters nor execution/lifecycle mutation services. The deterministic planner, validation pipeline, portfolio authority, readiness gates and kill switch retain exclusive control. `AI_GATED` resolves to `AI_OFF` in this release.

## Pipeline

An independent worker finds eligible persisted signals, builds a bounded immutable context from database truth, and calls a provider through a structured interface. The provider receives a versioned system prompt and sanitized JSON snapshot only. Strict validation converts any malformed, stale, unsupported or contradictory response to `ABSTAIN`. The ledger uses a stable snapshot/provider/prompt idempotency key.

Provider failure, timeout, circuit opening, cost ceiling, daily quota, disabled configuration and invalid model output are ordinary recorded abstentions. They never raise into the signal or execution workers.

## Modes

- `AI_OFF`: no provider request and no new decision.
- `AI_OBSERVE`: diagnostic analysis only.
- `AI_SHADOW`: records the counterfactual recommendation; default.
- `AI_ASSIST`: exposes recommendations to the operator but cannot mutate execution.
- `AI_GATED`: deliberately unavailable and mapped to `AI_OFF`.

## Prompt and response contract

Prompt version `ai-shadow-v1` requires structured output, uncertainty, contradiction analysis, abstention on insufficient evidence, no invented facts, no order commands, and a separation between setup quality and portfolio admission. Its text, checksum and response schema are persisted in `ai_prompt_versions`.

Only the enumerated action, direction, confidence, uncertainty, risk multiplier, evidence lists, invalidations, regime and concise explanation are accepted. Hidden reasoning and chain-of-thought are neither requested nor stored.

## Security

Secret-like keys and URLs are redacted recursively. Control characters and external text are bounded. The provider API key exists only in the outbound authorization header. Raw provider bodies, request headers, credentials, Telegram tokens and database URLs are never persisted or displayed. Model output is never evaluated as code, interpolated into SQL, or used as an execution instruction.

## Data and evaluation

`ai_decisions` is immutable and multi-user scoped. `ai_decision_outcomes` separates pure signal MFE/MAE/direction from deterministic policy, actual fills/fees/slippage, manual or panic intervention, and AI counterfactuals. Metrics report schema validity, abstention, distribution, latency, cost, agreement, accept/reject precision, Brier score, ECE and reliability buckets. Raw model confidence is labeled advisory until sufficient calibration samples exist.

## Render configuration

Keep `AI_PROVIDER=disabled` for the first deployment. Configure `AI_PROVIDER_ENDPOINT`, `AI_MODEL`, `AI_MODEL_VERSION`, and `AI_PROVIDER_API_KEY` as Render secrets only when enabling a compatible structured provider. Explicitly set request/user/global/cost/token/concurrency/context-age limits. Cost rates default to zero and must be configured from the provider price schedule before cost reporting is treated as authoritative.

## Rollout

1. Deploy disabled and verify migrations, commands, diagnostics and abstention records.
2. Enable a provider in `AI_OBSERVE`; verify redaction, latency, schema validity and cost accounting.
3. Move selected users to `AI_SHADOW`; collect out-of-sample outcomes across regimes.
4. Permit `AI_ASSIST` only for trained operators. Recommendations remain non-binding.
5. Do not implement `AI_GATED` until the evidence gate below is met in a later reviewed release.

Required evidence includes a statistically meaningful out-of-sample sample across symbols/regimes, stable schema validity and abstention, calibrated reliability, positive net counterfactual expectancy after fees/slippage, bounded disagreement losses, provider/model drift monitoring, adversarial prompt-injection testing, VST soak testing, incident/rollback drills and explicit human approval of deterministic maximum influence.

## Rollback

Set `AI_PROVIDER=disabled` or `AI_TRADING_MODE=AI_OFF` and restart. Per-user `/ai_disable` is durable. The additive AI tables and indexes may remain for audit and are ignored by v9.9.11 code. No execution rollback is required because v9.9.12 does not change execution behavior.

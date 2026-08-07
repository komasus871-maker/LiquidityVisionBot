# AI governance

Certification and governance are bound to a SHA-256 identity over provider, protocol, redacted full endpoint, model/model version, prompt version, schema version/checksum, context version, request-format version, pricing version, capability snapshot, requested/effective output mode, downgrade status, and reasoning effort.

Governance states are `UNVERIFIED`, `OBSERVING`, `SHADOW_CERTIFIED`, `ASSIST_CERTIFIED`, `SUSPENDED`, and `RETIRED`. Certification states are separate and explicitly reported. Passing certification enters observation; promotion is manual and evidence-gated. Evidence rows persist exact-identity sample and failure counts. Global history is diagnostic only.

`SHADOW_CERTIFIED` still has zero trading authority. `ASSIST_CERTIFIED` remains advisory. `AI_GATED` is unavailable. The durable global kill switch blocks only provider activation and does not mutate deterministic trading state.

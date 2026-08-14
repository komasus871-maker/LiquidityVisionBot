# LiquidityVisionBot v10.0.0 — Autonomous Market Intelligence Platform

## Authority boundary

“Autonomous” means continuous public-data observation, deterministic feature capture, immutable outcome attachment and scheduled research evaluation. It does not mean autonomous economic execution. GPT and research cannot place, modify, cancel or close orders; change risk, sizing, filters or portfolio state; or enable LIVE. `AI_GATED` remains fail-closed as `AI_OFF`.

## Pipeline

Confirmed market data flows through freshness classification, 4H context, 1H setup, 15M entry timing, Market Story, strategy suitability, evidence families, contradictions, Signal Quality V3, entry readiness and Ranking V4. Deterministic decisions may enter the existing PAPER copy lifecycle. Outcomes attach to immutable decision-time snapshots and feed clean cohorts, frozen hypotheses and chronological walk-forward research. GPT runs alongside this pipeline as an advisory red team.

## Reliability and data availability

AI observation rejects stale persisted context before provider invocation and records transport, extraction, JSON/schema, domain, market-truth and semantic stages separately. A failed provider result persists a safe fallback action without being reported as a model-produced abstention. Provider circuit failures are restricted to provider-originated classes.

The microstructure worker is enabled in `render.yaml`, lease-protected, bounded by symbol/sample/level limits and uses credential-empty BingX public endpoints. Valid depth persists even when public funding/OI is unavailable. Only aggregates are stored. `/data_health SYMBOL` reports feature-family availability.

## Strategy and quality

Research families include LIQUIDITY_SMC, TREND_CONTINUATION, BREAKOUT, MEAN_REVERSION, PUMP_REVERSAL, PUMP_CONTINUATION, LIQUIDITY_SWEEP_REVERSAL, SCALPING_TREND, SCALPING_BREAKOUT, SCALPING_MEAN_REVERSION and liquidity-sweep scalping. Assessments carry suitability, support, contradictions, confirmation, invalidation, target framework, uncertainty and data requirements.

Quality V3 is not a probability. It persists raw/normalized family components, correlated-evidence shrinkage, contradiction penalties, critical caps and uncertainty. Entry readiness is separate. Ranking V4 is advisory and has no production authority.

## Rollout and rollback

1. Deploy additive migrations with AI and LIVE disabled.
2. Confirm `/system_health` and the microstructure worker heartbeat.
3. Confirm `/data_health BTCUSDT`, `/orderbook BTCUSDT`, `/funding BTCUSDT`, and `/open_interest BTCUSDT` after one collection cycle.
4. Leave AI off until the current provider identity is deliberately recertified; never reuse an expired certification.
5. Observe PAPER accounting and research capture before enabling optional AI_OBSERVE.

Rollback by setting `MICROSTRUCTURE_COLLECTION_ENABLED=false`, `AI_TRADING_MODE=AI_OFF`, `AI_PROVIDER=disabled`, and keeping `LIVE_EXECUTION_ENABLED=false`. Additive V10 rows can remain; legacy immutable rows are not rewritten.

## Evidence limitations

Funding percentiles, OI deltas, exit-policy comparisons, threshold selection, feature edge, hypotheses and walk-forward recommendations remain `INSUFFICIENT_HISTORY` until enough trustworthy forward samples exist. No single winner changes production weights or gates.

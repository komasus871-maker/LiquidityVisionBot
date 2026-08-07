# LiquidityVisionBot v9.9.18 — Market Intelligence & Signal Quality

## Release contract

This release adds market understanding and research data, not economic authority. The legacy analyzer finalizes its direction, entry status, plan geometry and deterministic decision before `MarketIntelligenceEngine` runs. The resulting envelope is advisory metadata only. Planner, copy admission, risk, sizing, accounting, position lifecycle and exchange order code do not import or consume it.

`AI_GATED` remains mapped to `AI_OFF`. LIVE remains independently gated. Automatic PAPER copy remains unchanged.

## Architecture

The deterministic flow is:

```text
confirmed market data
  → 4H context / 1H setup / 15M entry roles
  → structure and quality-scored level clusters
  → unresolved/consumed liquidity map
  → momentum and trend maturity
  → bounded optional microstructure interaction
  → market story
  → contradictions and confidence decomposition
  → invalidation and target realism
  → Signal Quality V2 / Strategy Suitability / Ranking V3
  → immutable research snapshot
```

Single-timeframe production analysis records the available timeframe honestly. `MultiTimeframe.analyze_intelligence()` exposes the complete 4H → 1H → 15M hierarchy. A fresh pre-existing public microstructure aggregate is projected into the advisory decision snapshot after the legacy decision is final; stale and future-dated aggregates are excluded. Missing benchmark, funding, open-interest or order-book data is stored as unavailable; no value is fabricated.

## Engines

- Market Story: deterministic states and transitions such as trend continuation, pullback, breakout attempt, liquidity sweep, momentum exhaustion, pump/dump exhaustion, range and uncertain.
- Level Intelligence: confirmed swings, previous day/session references, round levels and unmitigated FVG boundaries. Overlapping representations are clustered; evidence is aggregated once per independent family.
- Liquidity Map: above/below unresolved versus consumed clusters, freshness, significance, proximity and an explicit likely attractor.
- Structure/Zone Quality: close versus wick breaks, displacement, follow-through, retest/reclaim, sweep class, FVG freshness/mitigation and order-block origin displacement.
- Momentum/Trend: velocity, acceleration, RSI slope, MACD histogram change, body efficiency, volume, ATR travel, failed continuation and trend maturity.
- Reversal Research: `PUMP_REVERSAL_*` and `DUMP_REVERSAL_*`, including explicit continuation risk. Re-entry is bounded, requires new evidence and never increases risk after a loss.
- Signal Quality V2: family-level aggregation, contradiction penalties, critical caps, evidence diversity and separate data/direction/setup/entry/invalidation/target/execution confidence.
- Ranking V3: persists Quality V2 with strongest advantages, weaknesses, contradictions and uncertainties. `/signal_rankings` explains the #1/#2 quality margin but never changes production priority or admission.
- GPT red team: receives a bounded immutable subset and is asked to challenge evidence, timing, invalidation, target realism, context completeness and abstention. Supporting and contradictory evidence use disjoint stable IDs; explicit unique ranks cover supporting evidence only, strongest first, with a deterministic evidence-ID tie-break. No reasoning trace is requested or persisted.

## Database migration

`create_tables()` adds two repeatable tables:

- `market_intelligence_snapshots`: one immutable versioned row per signal and intelligence version, with normalized query columns plus complete JSON provenance.
- `microstructure_aggregates`: bounded normalized interaction features, checksums, freshness and expiry. Raw bids and asks are prohibited.

Both tables and their indexes use backend-neutral SQL supported by SQLite and PostgreSQL. Existing signal and research rows are not rewritten. Older code ignores these additive tables, so rollback is safe. Do not delete them during rollback; they remain inert historical research evidence.

## Telegram

- `/market_story SIGNAL_ID`
- `/signal_quality SIGNAL_ID`
- `/contradictions SIGNAL_ID`
- `/liquidity_map SYMBOL`
- `/orderbook SYMBOL`
- `/pump_reversals`
- `/rr_research`
- `/entry_research`
- `/reentry_research`
- `/quality_report`

`/trade SIGNAL_ID` includes only the concise story, Market/Signal Quality and evidence-diversity summary.

## Safe deployment

1. Deploy with copy, LIVE and AI modes unchanged.
2. Let additive migrations complete.
3. Confirm `research_engine` and `microstructure_observer` startup logs.
4. Use the Render values in `.env.example`/`render.yaml`. Public depth collection is bounded to eight relevant open symbols, five samples per symbol, fifty levels per side and a sixty-second cycle.
5. Run the Telegram checks above.
6. Confirm `/copy_stats`, `/positions`, `/live_readiness bingx` and existing AI diagnostics are unchanged.
7. If AI observation is configured, verify `AI_SCHEMA_VERSION=ai-decision-v3`, then deliberately run `/ai_certification run` once: the red-team prompt and evidence contract have a new immutable identity, so an older certification must not qualify it. The command is the only paid step and is never run automatically. Keep `AI_GATED` unavailable.

PASS means new snapshots are present, old copy/accounting counts agree, Quality V2 says it is not a probability, no raw order book appears in the database, observer failures do not stop normal analysis, and no new execution authority exists.

Early `INSUFFICIENT_SAMPLES`, unavailable benchmark/OI/funding, or no qualified walls are expected states—not release failures.

## Research limits

No profitability claim is made. Current samples are too small for production weight or threshold changes. Accumulate immutable enriched decisions and ordered outcomes, then use chronological frozen-hypothesis validation. Every filter report must count missed winners as well as avoided losses.

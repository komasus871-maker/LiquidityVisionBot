# v7.4 — Intelligence Integration

## Architecture
- Added a unified `IntelligenceLayer` read model combining Trade DNA, similarity evidence, historical outcomes and AI Memory.
- Added startup backfill for missing memories on old closed trades.
- Added modular Replay PRO intelligence cards to keep Telegram payloads under message limits.

## Replay PRO
- Trade DNA is now the primary setup card.
- Added AI Memory: what worked, what failed, strengths, weaknesses and lesson learned.
- Added Similar Trades 2.0, expected R, reliability, best/worst match and historical intelligence.
- Added entry quality, risk quality, execution rating and setup grade.

## Similarity 2.0
- Best/Worst Match now refer to highest/lowest similarity rather than trade result.
- Best/Worst Trade remain outcome-based and are separate fields.

## Scanner 4.0
- Replaced overlapping sections with one deduplicated global ranking.
- Each symbol appears only once with its strongest execution category.

## Operations
- AI Memory backfill is bounded by `MEMORY_BACKFILL_LIMIT` (default 500) per startup.
- Release archive excludes secrets, VCS metadata, caches, local databases and duplicate project trees.

# Liquidity Vision v6.1 — Intelligence Stabilization

## Added
- Canonical intelligence snapshots stored in `intelligence_snapshots`.
- LIVE, Journal and Replay read the same latest snapshot.
- Numeric Trade Health score plus category.
- Confidence/component deltas and smarter reasons.
- Confidence and health timeline persistence.
- `learning_samples` foundation for future Probability/Similarity releases.
- Watchlist no longer shows misleading `INITIALIZING · 0/0`.

## Database
Migrations are automatic through `create_tables()`.

## Test focus
- Active trade LIVE confidence equals `/trade` confidence.
- Journal health/confidence equals latest intelligence snapshot.
- Closed signals create one `learning_samples` row.
- Watchlist pending state shows a progress message instead of zero values.

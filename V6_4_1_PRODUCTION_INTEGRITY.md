# Liquidity Vision v6.4.1 — Production Integrity Hotfix

## Fixed
- Explain Pro and Similar Setups now reuse the exact analysis snapshot shown to the user.
- Callback actions preserve the selected timeframe instead of silently recalculating 1H.
- Current market price is separated from planned entry.
- Pullback plans are rebuilt around the planned entry zone.
- LONG/SHORT geometry is validated before a signal may be persisted.
- Invalid plans are blocked with `PLAN INVALID` and are not recorded.
- Setup score now reflects execution readiness rather than copying directional conviction.
- Directional conviction is no longer presented as statistical probability.
- Dynamic confidence is smoothed to a maximum 18-point change per monitor cycle.
- Manual closes are stored as `INVALIDATED` with result `MANUAL_STOP`, so they do not count as stop losses.
- Probability cases support weighted effective sample size and Wilson intervals.

## Trade geometry invariants
- LONG: `stop < entry < tp1 < tp2 < tp3`
- SHORT: `tp3 < tp2 < tp1 < entry < stop`

## Verification
- Full test suite: 27 passed.

# Liquidity Vision v6.2 — Trade Manager & Probability Alpha

## Integrity fixes
- Added a central `TradeManager` as the single market-integrity checkpoint.
- Reconciles duplicate or opposite open plans by `user + symbol + timeframe`.
- Preserves a blocked opposite scenario as a Candidate instead of a second Trade.
- Signal Tracker reconciles markets before every lifecycle pass.
- Journal reconciles the current user's markets before rendering statistics.
- Existing database uniqueness remains the final safety guard.

## Probability Alpha
- Similar historical cases are weighted by similarity instead of counted equally.
- Shows raw matched cases and effective sample size.
- Adds honest Wilson uncertainty ranges for TP1 and STOP.
- Does not display estimates until the weighted effective sample is large enough.
- Adds weighted average duration and realized R to the probability context.

This release is intentionally conservative: historical estimates are observations, not guarantees.

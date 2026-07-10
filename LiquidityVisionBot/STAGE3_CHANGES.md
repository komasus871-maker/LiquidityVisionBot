# Stage 3 — Analytics Engine

- Replaced WAIT-only output with separate market bias, recommendation and execution status.
- Added READY / WAIT FOR TRIGGER / WATCHLIST / OBSERVE states.
- Added activation conditions for non-ready setups.
- Fixed Premium/Discount to a bounded 0–100 dealing-range position.
- Reworked displacement into weak/moderate/strong with body efficiency and expansion ratio.
- Added RSI, entry-location, volume and counter-trend risk handling.
- Scanner now ranks by composite score and includes bias, execution state, confirmations and primary risk.
- Conditional setups can be stored and tracked; pure observations are not recorded as trades.

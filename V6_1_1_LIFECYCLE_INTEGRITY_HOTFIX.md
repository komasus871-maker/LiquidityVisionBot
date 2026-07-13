# Liquidity Vision v6.1.1 — Lifecycle Integrity Hotfix

- Serializes signal promotion per user/symbol/timeframe with a database lease.
- Reconciles legacy opposite/duplicate open trades and preserves the earliest real active trade.
- Adds `/trade ID stop`, `/stop ID`, and `/stop trade ID` manual tracking controls.
- Manual stops are saved as `MANUAL_STOP` and do not pollute Stop Loss statistics.
- Smooths Dynamic Confidence and adds Trade Health hysteresis to prevent minute-by-minute oscillation.
- Adds a 15-minute cooldown for non-critical intelligence alerts while critical AT RISK / 90% risk alerts remain immediate.

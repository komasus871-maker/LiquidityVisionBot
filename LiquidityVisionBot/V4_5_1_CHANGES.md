# v4.5.1 — Universal Analyze FSM Fix

- `/analyze SYMBOL [timeframe]` now works even when the bot is waiting for a ticker.
- Plain ticker input still opens timeframe selection.
- Accidental `-analyze SYMBOL` input is accepted as a convenience.
- `/analyze` clears stale FSM state before starting a fresh analysis.
- Empty input and invalid timeframe receive explicit messages.

# Liquidity Vision v5.2 — Trade Core & Confidence

## Lifecycle integrity
- Enforces one open trade plan per user, symbol and timeframe at the database level.
- Reconciles legacy duplicate LONG/SHORT plans on startup and keeps the most advanced lifecycle.
- Opposite analysis during an ACTIVE/TP1/TP2 trade is stored as a Candidate, not a second Signal.
- Candidates remain linked to the active trade and can be promoted after the trade closes.

## Replay 2.0
- Human-readable lifecycle labels.
- Shows elapsed time between meaningful events.
- Hides noisy refresh events.
- Displays plan updates, direction flips, break-even and closure events clearly.

## Live trade UX
- Replaces ambiguous stop “safety” with Risk used and Distance to SL.
- Adds an overall Confidence bar.
- Adds Trend, Structure, Liquidity and Momentum component bars.

## Journal
- Adds Alternative Candidates section.
- Existing duplicate open plans are automatically invalidated during migration.

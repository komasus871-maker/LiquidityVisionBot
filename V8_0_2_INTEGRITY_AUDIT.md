# LiquidityVisionBot v8.0.2 — Integrity Audit

- Resolves stale alternative-candidate locks when the blocking trade is no longer ACTIVE/TP1/TP2.
- Calculates win rate from resolved win/loss outcomes instead of silently treating unknown records as losses.
- Exposes break-even and unclassified terminal records in Journal and Profile.
- Adds `/trade stats audit` for terminal-record and duplicate-open-plan diagnostics.
- Shows timeframe in Recent lifecycle rows.

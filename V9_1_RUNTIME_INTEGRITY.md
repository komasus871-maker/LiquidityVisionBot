# LiquidityVisionBot v9.1.0 — Runtime Integrity & Resilient Analysis Core

## Production changes

- Restored the missing `services.brain` runtime contract through the current Decision Brain instead of duplicating legacy scoring logic.
- Made EMA, RSI, and MACD available through a typed pandas fallback when the optional `ta` package is unavailable.
- Added centralized release-integrity validation for version drift, missing release files, and missing runtime modules.
- Synchronized runtime version metadata across code, documentation, deployment examples, handlers, and regression tests.
- Added v9.1 regression coverage for the fallback indicator backend, scanner compatibility facade, and release integrity.

## Safety

Live trade execution remains hard-disabled. Paper copy execution and validation behavior are unchanged.

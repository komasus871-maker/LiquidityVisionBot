# LiquidityVisionBot v7.6 — Unified Core Foundation

## Implemented

- Added canonical `AnalysisContext` and `AnalysisIdentity` shared across analysis consumers.
- Added ordered Unified Analysis Pipeline: Market → Structure → Liquidity → Volume → Momentum → Regime → Trade DNA.
- Extracted reusable Market, Structure, Liquidity, Volume, Momentum and Regime services.
- Added thread-safe bounded TTL/LRU analysis cache with stable candle-frame fingerprints.
- Refactored legacy `Analyzer` to consume the unified pipeline without changing its public output contract.
- Added compatibility metadata and Trade DNA foundation to analysis output.
- Routed Analyze, Scanner, Scanner V2, Scanner Engine, Multi-Timeframe, Watch Engine and Observation Monitor through the shared runtime/core.
- Added off-event-loop execution for Multi-Timeframe and legacy Scanner Engine paths.
- Added unified-core regression and cache tests.

## Validation

- Full test suite: 56 passed.

# Market Data Contract

Phase 2A establishes the single candle-integrity boundary for trade-capable analysis. It is an input-truth contract, not a strategy or profitability change.

## Runtime path and ownership

```text
OKXProvider.get_klines (public REST; no fallback/websocket candle source)
  -> Market.get_klines (provider-aware 20-second cache)
  -> DataIntegrityEngine.prepare_market_frame
  -> analysis_runtime.run_analysis
  -> UnifiedAnalysisPipeline.execute (rechecks boundary, requires 220 closed candles)
  -> Analyzer.analyze
  -> ProbabilityEngine.enrich
  -> DecisionQualityEngine.enrich
  -> APPROVED or NO_TRADE
  -> SignalRecorder (only APPROVED can promote)
```

Manual analysis, `Scanner`, `WatchEngine`, `ObservationMonitor`, `ScannerEngine`, `ScannerV2`, and `MultiTimeframe` all call `Market.get_klines` and `run_analysis`; they therefore share this boundary. `SignalTracker` reads the same provider frame for lifecycle prices and calls the compatibility `validate_market_frame` wrapper. PAPER and LIVE do not source candles independently: they consume persisted, Phase 1B-approved signals via `CopyExecutionPlanner` and `ExecutionValidator`. Research/backtest callers sharing `Analyzer` receive structural checking but are marked `UNVERIFIED_DIRECT_INPUT` unless their frame is explicitly provider-marked; they cannot thereby claim runtime freshness.

## Canonical frame

The runtime representation remains a pandas `DataFrame`, rather than a new Candle model. Required columns are `time` (or a supported timestamp alias), `open`, `high`, `low`, `close`, and `volume`; normalized output uses UTC `time` open timestamps in chronological order. A provider-marked runtime frame must have a supported canonical timeframe and is checked for:

1. finite numeric, non-negative OHLC prices and non-negative volume;
2. `high >= max(open, close, low)`, `low <= min(open, close, high)`, and `high >= low`;
3. unique open timestamps and exact timeframe spacing for continuous crypto markets;
4. no future open timestamp relative to the supplied/injected UTC reference clock;
5. at least the consuming window's required closed-candle history (220 for the unified trading pipeline).

`utils.timeframe.normalize_market_timeframe` and `TIMEFRAME_SECONDS` are the canonical duration resolver. It recognizes explicit aliases (for example `60m` -> `1h`) and returns no fallback for unknown input. Provider spellings (`1H`, `4H`, and so on) stay at the OKX edge.

## Closed-candle semantics

OKX public history may include a forming bar and labels closure with `confirm`. `OKXProvider` declares `INCLUDES_FORMING`; `prepare_market_frame` removes every non-confirmed row before analysis and records `forming_candles_removed`. Normal trading analysis is consequently `CLOSED_ONLY`. A frame without a provider closure flag is only `CLOSED_ONLY_ASSUMED` for direct/test callers and is never used to weaken a provider runtime check.

## Validation states and behavior

The quality envelope on `DataFrame.attrs["market_data_quality"]` and analysis output `data_quality` has `status`, `code`, `reason`, and provenance/diagnostics. Final states are `VALID`, `STALE`, `INCOMPLETE`, `GAPPED`, `CONFLICTING`, `INSUFFICIENT`, `PROVIDER_ERROR`, `UNKNOWN`, or `INVALID`. Exact duplicates are deterministically removed, with `exact_duplicates_removed`; an unambiguous reverse/out-of-order sequence is stable-sorted and records `ordering=OUT_OF_ORDER_NORMALIZED`. Same-timestamp candles with differing OHLCV values are `CONFLICTING`, never silently selected.

Gaps or irregular spacing are `GAPPED`; no synthetic candles are created. Malformed timestamp, missing OHLCV, NaN/infinity, invalid geometry, negative data, and future data fail closed. `UNKNOWN` covers unsupported/missing timeframe; provider exceptions, malformed provider parses, and unexpected empty frames are explicitly `PROVIDER_ERROR` or `INSUFFICIENT`, never a valid empty default.

## Freshness and cache

Freshness uses `last_open + timeframe_duration` as the latest expected close and a 1.25-candle-duration publication allowance. It is evaluated against an injectable UTC reference time; there is no global five-minute threshold. A 1-minute and a 4-hour sequence therefore have different permitted ages. `Market` caches only a structurally valid normalized provider frame for 20 seconds, keyed by provider type, normalized symbol, canonical timeframe, and requested limit. On every cache read it runs the integrity boundary again, so cache retrieval never resets timestamp freshness or permits stale candles as fresh. Returned frames are copied before use to prevent shared mutable cache state.

## Provenance and authority

The envelope carries provider, symbol, canonical timeframe, requested limit, first/last candle timestamps, reference timestamp, closed-forming semantics, fallback flag (currently always false: no fallback exists), cache flag, and validation diagnostics. Valid data proceeds to `Analyzer`; invalid data returns a legacy-shaped `NO_TRADE` analysis envelope without executing feature/strategy stages. Explicit invalid runtime data states are vetoed by `DecisionQualityEngine`; an `UNKNOWN` boundary result is already a NO_TRADE envelope. `SignalRecorder` therefore persists at most an observation and `DecisionQualityEngine.authorization` blocks planner/PAPER/LIVE promotion.

No private exchange credentials, public network fixture, order submission, strategy formula, indicator parameter, confidence threshold, or profitability rule was changed by this contract.

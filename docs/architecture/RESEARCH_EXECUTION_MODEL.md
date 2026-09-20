# Research Execution Model

`HistoricalReplayEngine` is a deterministic, no-I/O historical execution model. Its output is always `HISTORICAL_REPLAY`, never PAPER or LIVE.

## Entry

- `MARKET_NEXT_OPEN`: after a closed-candle decision, fills at the next candle open adjusted by the configured modeled market slippage rate.
- `LIMIT_AFTER_DECISION`: a limit exists only from the next candle onward. A favorable gap improves the modeled limit fill; a pre-decision candle cannot fill it.
- `intended_entry`, `simulated_fill`, and `actual_fill` are separate. Replay never invents `actual_fill`.

## Exit and ambiguity

Stops and the first configured target are evaluated after fill eligibility. A gap through a stop/target fills at the next candle open, rather than the desired stop/target price. If the same OHLC candle touches both stop and target, the model records `STOP_TARGET_SAME_CANDLE_CONSERVATIVE_STOP_FIRST` and chooses the stop. For a limit filled inside an OHLC candle, its unknown intrabar path is flagged; target credit from that entry candle is withheld, while a same-candle stop is treated conservatively.

MFE/MAE include only unambiguous movement while a trade is active. The limit-entry candle's pre-fill range and all exit-candle/post-exit extrema are excluded.

## Costs and capital semantics

Gross PnL is price movement on one notional unit. Entry and exit fees are modeled separately and net PnL is `gross - fees - funding`; modeled slippage changes the market fill price instead of being silently treated as a successful close-price fill. Funding is `NOT_MODELED` unless a supplied per-bar model exists. No exchange-accurate funding, spread, liquidation, leverage, or actual-fill claim is made without corresponding source data.

Position size and leverage are deliberately outside this replay's strategy-edge metric: leverage does not create predictive edge. Performance modes are immutable provenance categories (`CONCEPTUAL`, `HISTORICAL_REPLAY`, `PAPER`, `SHADOW`, `LIVE`) and `PerformanceAttribution` rejects mixed-mode aggregates.

The aggregate layer also cross-checks the declared mode against ledger provenance. `LIVE` requires both an `actual_fill` and explicit `EXCHANGE_ACTUAL` execution evidence; a historical or PAPER artifact cannot become LIVE evidence through relabeling. Conversely, non-LIVE outcomes cannot carry an actual exchange fill.

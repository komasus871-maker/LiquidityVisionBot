# Flow / Microstructure Alpha Master

Status: bounded historical program complete; no candidate qualified.

## What changed

The research stack now supports genuine exchange-reported aggressor flow. Binance USD-M 5-minute klines expose total and taker-buy base/quote volume; taker-sell volume is the exact residual, delta is taker buy minus taker sell, and CVD is the chronological cumulative delta. Candle direction is never used to assign aggressor side.

The immutable materialization combines that source with venue-matched OI metrics and settled funding for BTCUSDT, ETHUSDT, and SOLUSDT. It preserves provider, source timestamps, availability timestamps, integrity states, and content hashes. OI gaps are retained as `GAPPED`; they are not filled or interpreted as zero. Funding has no detected gaps in the materialized interval.

Historical liquidation events were not available from a certifiable official archive and therefore were not backtested. The available historical `bookDepth` archive is percentage-bucket depth sampled at roughly 30 seconds, not reconstructable raw L2; it was excluded from primary alpha. Raw trades/aggTrades and bookTicker archives exist but are substantially larger and were not required because the certified kline aggregate already reports real taker-buy flow.

## Architecture

- `tools/materialize_flow_alpha.py` creates deterministic, hash-addressed research datasets once.
- `services/flow_microstructure.py` owns integrity validation, true delta/CVD, completed-bar aggregation, causal features, and the fail-closed Shadow gate.
- `services/flow_alpha_lab.py` owns the 12 frozen directional Development experiments.
- `services/flow_relative_value_lab.py` owns the one permitted 9-candidate cross-sectional Development cycle.
- `DecisionQualityEngine` authorizes every candidate template. The research modules never grant execution authority and are not imported by runtime production paths.
- `tools/finalize_flow_alpha_registry.py` validates hashes, frozen rosters, cost scenarios, access flags, and rejection state before producing the 21-experiment registry.

## Protected chronology

- `FLOW_DEV`: 2023-01-01 through 2025-09-30, opened for outcome research.
- `FLOW_VALIDATION`: 2025-10-01 through 2026-01-31, materialized and sealed.
- protected gap: 2026-02-01 through 2026-06-30, preserving the prior derivatives Blind interval.
- `FLOW_BLIND`: 2026-07-01 through 2026-08-31, materialized and sealed.

No outcome loader for Validation or Blind was used. `LEGACY_SEEN_TEST`, prior `BLIND_HOLDOUT`, and derivatives protected samples remained inaccessible.

## Bounded result

Cycle A evaluated exactly four directional families with three broad threshold variants each. All 12 failed Development. Cycle B was preregistered only after Cycle A failed, evaluated three equal-notional relative-value families with three variants each, and all nine failed Development. No candidate entered Validation; Blind therefore remained locked.

Historical alpha mining stops here under the master protocol. The next justified evidence acquisition is forward archival of event-level public trades, liquidations where the venue provides trustworthy live events, and reconstructable book snapshots. That collector is not connected to production execution and must begin with a separately preregistered Shadow evidence contract.

Production defaults, champion behavior, PAPER/LIVE/copy paths, and Telegram signal behavior are unchanged. No live-data collector, Shadow runner, testnet order path, private credential, or real order was enabled.

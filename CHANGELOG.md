# Changelog

## 9.8.5 — BingX Read-Only Reachability

- Added a read-only BingX USDT-M perpetual adapter for health, contract rules, balances, positions, and open orders.
- Added Render reachability diagnostics through BingX public server time.
- Added `BTCUSDT` → `BTC-USDT` normalization and cached public contract rules.
- Reused bounded retries, split timeouts, safe non-JSON diagnostics, and fail-fast authentication handling.
- Added BingX environment configuration while keeping credentials environment-only.
- Preserved the strict read-only contract: no order placement, modification, or cancellation methods exist.
- Added v9.8.5 regression coverage and release documentation.

## 9.8.4 — Resilient Exchange Transport

- Added bounded exponential-backoff retries for OKX timeouts, transient transport failures, rate limits, malformed responses, and HTTP 5xx failures.
- Added separate connect and read timeout configuration for more reliable Render networking.
- Added typed timeout, rate-limit, and response errors while preserving the normalized ExchangeError contract.
- Added a short-lived process cache for public OKX symbol rules to reduce redundant exchange requests.
- Kept authentication/configuration failures fail-fast and preserved the strict read-only exchange contract.
- Added v9.8.4 regression coverage and deployment configuration documentation.

## 9.8.3 — Exchange Reachability: OKX Read-Only

- Added a read-only OKX V5 adapter for public health, swap instrument rules, balances, positions, and pending orders.
- Added OKX Demo Trading support through `x-simulated-trading: 1`; API credentials remain environment-only.
- Added automatic `BTCUSDT` → `BTC-USDT-SWAP` normalization while accepting native OKX instrument IDs.
- Added robust non-JSON/HTML response diagnostics with HTTP status and a bounded safe preview.
- Added OKX as an independently diagnosed exchange and made it the default in the example configuration.
- Preserved the read-only contract: no order placement, modification, or cancellation methods exist.
- Added v9.8.3 regression coverage and release documentation.

## 9.8.2 — Multi-Exchange Foundation: Bybit Read-Only

- Added a working read-only Bybit V5 adapter for health, Unified wallet balances, linear positions, open orders, and symbol rules.
- Added independent exchange health classification: CONNECTED, PUBLIC ONLY, NOT CONFIGURED, GEO BLOCKED, AUTH FAILED, and UNAVAILABLE.
- Binance HTTP 451 restricted-location responses now report GEO BLOCKED instead of PUBLIC ONLY.
- Added optional exchange routing to all exchange Telegram commands and `EXCHANGE_DEFAULT` configuration.
- Added safe endpoint diagnostics without exposing credentials.
- Preserved the read-only contract: no adapter can place, modify, or cancel orders.
- Added v9.8.2 regression tests and updated deployment documentation.

## 9.8.1 — Exchange Foundation: Binance Read-Only

- Added a typed, async, read-only `ExchangeAdapter` contract and normalized exchange models/errors.
- Added an environment-backed exchange registry that never persists API credentials.
- Added a working Binance USD-M Futures adapter for health, balances, positions, open orders and symbol execution rules.
- Added `/exchanges`, `/exchange_balance`, `/exchange_positions`, `/exchange_orders`, and `/exchange_symbol`.
- LIVE execution remains impossible by contract: no order write methods exist in this release.
- Added mocked exchange regression tests and deployment configuration documentation.

## 9.8.0 — Explainable Similarity Intelligence

- Added feature-level similarity explanations with weighted Structure, Liquidity, Market, Indicators, and Execution breakdowns.
- Added matched-feature and difference attribution for every closest Replay.
- Added aggregate top matches, largest differences, average similarity, and sample-based statistical confidence.
- Corrected report semantics so `Found` and performance metrics use the complete qualifying history while Telegram output remains bounded.
- Added `/genome [signal_id]` for grouped inspection of the normalized Strategy Genome and fingerprint.
- Preserved leakage protection: only closed paper and resolved zero-exposure shadow outcomes are eligible.
- Preserved all copy guardrails, portfolio accounting, adaptive policy boundaries, and fail-closed LIVE execution.
- Added v9.8 regression coverage and updated production documentation.

## 9.7.0 — Strategy Genome & Similar Trade Intelligence

- Added deterministic Strategy Genome snapshots for every accepted and rejected copy attempt.
- Added full-context similarity scoring across structure, liquidity, regime, timeframe, setup, indicators, volatility and execution features.
- Added leakage-safe search over closed paper positions and resolved zero-exposure shadow outcomes only.
- Added `/copy_similar [signal_id]` with Win Rate, average R, MFE, MAE and closest Replay IDs.
- Added indexed genome persistence with additive SQLite/PostgreSQL-compatible migrations.
- Preserved all guardrails, adaptive policy limits and fail-closed LIVE execution.
- Added v9.7 regression coverage; full suite passes with 102 tests.

## 9.5.0 — Guardrail Outcome Intelligence

- Added zero-exposure shadow lifecycle tracking for rejected paper executions.
- Added counterfactual R attribution by rejection guardrail.
- Added `/copy_guardrails` report for losses avoided, wins missed, and net shadow expectancy.
- Added additive database migration fields with PostgreSQL and SQLite compatibility.
- Preserved fail-closed LIVE execution and all existing risk limits.


## 9.4.0 — Execution Intelligence & Rejection Analytics

- Added a read-only copy-execution decision funnel for accepted and rejected attempts.
- Added ranked rejection diagnostics by guardrail code, symbol, and timeframe.
- Added dominant rejection reason to copy status and statistics.
- Added `/copy_rejections` with recent rejected attempts and 30-day acceptance rate.
- Kept all analytics observational: no guardrail is weakened automatically.
- Added v9.4 regression coverage and release documentation.

## 9.3.0 — Copy Training Foundation

- Added leakage-safe `CopyTrainingService` trained exclusively on closed paper executions.
- Added conservative Bayesian cohort policy with minimum samples, bounded confidence adjustment and bounded risk scaling.
- Integrated adaptive policy into the fail-closed execution validator.
- Added persistent negative-cohort rejection through `NEGATIVE_COHORT_EDGE`.
- Added training metadata to execution audit events.
- Added `/copy_training` reporting for readiness and cohort performance.
- Added v9.3 regression coverage and release documentation.

## 9.2.0 — Copy Execution Ledger & Portfolio Guardrails

- Rebuilt paper copy execution around an idempotent lifecycle ledger.
- Enforced daily realized-loss limits, portfolio heat, maximum simultaneous positions, duplicate-symbol protection, and post-trade cooldowns.
- Enforced minimum signal confidence, maximum activation slippage, and maximum notional exposure per position.
- Added equity-aware sizing: new trades use paper balance plus realized PnL rather than a static balance.
- Added correct partial-fill accounting for TP1 and TP2, including realized R and realized PnL deltas.
- Added event-level PnL ledger entries for opens, partial fills, closes, rejections, and panic closes.
- Added execution statistics for equity, daily PnL, total PnL, win rate, average R, and rejection counts.
- Added `/copy_guard` for confidence, notional, cooldown, and slippage guardrails.
- Added forward-compatible database migrations for all new profile, position, and event fields.
- Added v9.2 regression coverage and a full ACTIVE → TP1 → TP2 → TP3 database smoke test.

## 9.1.0 — Runtime Integrity & Resilient Analysis Core

- Stabilized runtime imports and analysis dependencies.

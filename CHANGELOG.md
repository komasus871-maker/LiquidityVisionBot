# Changelog

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

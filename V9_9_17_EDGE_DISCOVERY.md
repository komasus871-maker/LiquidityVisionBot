# LiquidityVisionBot v9.9.17 — Edge Discovery & Trading Intelligence Engine

## Release objective

v9.9.17 turns the immutable v9.9.16 research projections into a reproducible edge-discovery system. It measures and rejects hypotheses; it does not change the deterministic production strategy, copy policy, sizing, risk, positions, accounting, or exchange execution.

AI remains advisory. `AI_GATED` remains mapped to `AI_OFF`. All new strategies, rankings, models, hypotheses, RR policies and exit policies are PAPER/SHADOW research artifacts.

## Data boundary

`research_signal_snapshots` remains the immutable source of decision-time truth. `research_feature_vectors` normalizes a strict whitelist into `research-features-v2` and explicitly records missing fields.

Quality classes:

- `TRUSTWORTHY_DECISION_TIME`: eligible for evidence.
- `RECONSTRUCTED`: retained but excluded from forward-quality evidence.
- `LATE_BACKFILL`: captured after terminal state and excluded.
- `INCOMPLETE`: essential decision fields are missing.
- `CONTAMINATED`: future/outcome keys appear inside the supposed decision snapshot.

Automatic global discovery uses global snapshots only. Per-user reports use global data plus that user's own snapshots. Private records are not pooled into globally visible hypotheses.

## Outcome layers

Append-only research outcomes now expose four independent layers:

1. Pure market/signal: signal R, MFE/MAE in percent and R, TP progression, stop touch, and whether ordered path data exists.
2. Deterministic policy: theoretical policy R, partials, breakeven and trailing observations.
3. Execution: fills-derived PnL, R, fees and slippage.
4. Human intervention: manual/panic/early signal or execution actions.

A manual signal closure is excluded from pure-market evidence. A user's manual execution close does not convert an independently resolved market signal into a setup loss.

## Statistical methods

The engine reports N, wins, losses, breakeven, win rate, expectancy, median R, average win/loss, payoff ratio, profit factor, MFE/MAE, drawdown proxy, standard deviation and downside deviation. Sharpe-like and Sortino-like ratios require at least 30 samples.

Expectancy intervals use deterministic seeded bootstrap resampling. Defaults are conservative:

- fewer than 20: `VERY_LOW` / `INSUFFICIENT`
- 20–49: `LOW` / `EXPLORATORY`
- 50–99: `MODERATE`
- 100 or more: `HIGH`

Positive historical expectancy alone never produces `SUPPORTED` evidence.

## Feature and combination discovery

Binary feature comparisons report present versus absent outcomes. When data permits, comparisons retain only strata containing both groups across timeframe, regime and direction. The report remains descriptive and non-causal.

Two- and three-feature mining uses deterministic feature ordering, bounded tests, minimum support, minimum samples and a Bonferroni awareness field. All mined combinations are `EXPLORATORY` until frozen forward validation succeeds.

Negative findings are emitted only as `POSSIBLE_EXCLUSION_CANDIDATE`; they never block a production trade.

## Quality model and walk-forward validation

The first research quality model is an interpretable L2-regularized logistic model over versioned normalized features. Training and validation are strictly chronological. Each fold records:

- training and validation ranges;
- sample counts;
- Brier score and calibration error;
- selected-versus-naive baseline evidence;
- degradation from in-sample to later validation;
- coefficients, target definition, release and dataset cutoff.

No random train/test split is used.

## Hypothesis lifecycle

Bounded discovery freezes exact filter and comparator JSON, a feature/algorithm version, discovery window and cutoff. Signals strictly after the frozen cutoff form the forward cohort.

Lifecycle states are `FORWARD_TESTING`, `CONFIRMED` and `REJECTED`; historical artifacts retain discovery evidence independently. `CONFIRMED` requires the configured forward sample minimum, a positive bootstrap lower bound, and improvement over the frozen comparator. Definitions cannot be rewritten after seeing forward results.

No lifecycle state promotes a hypothesis into production.

## Strategy, ranking and portfolio research

Strategy Lab now includes the naive eligible-signal baseline alongside Liquidity/SMC, trend, breakout and mean-reversion candidates. A regime selector ranks them using only earlier same-owner/global outcomes and persists a non-economic recommendation.

Signal Ranking v2 records shrunk setup expectancy, confidence calibration, regime fit, setup evidence, portfolio-overlap penalty, estimated execution cost and human-readable reasons. It cannot submit an order.

Portfolio research measures clustered 15-minute signals, same-direction exposure and repeated symbols. BTC beta and sector conclusions remain explicitly unavailable until synchronized immutable returns and a versioned sector taxonomy exist.

## Scalping, RR and exit research

Scalping Lab separates 1m/3m/5m trend, breakout, mean-reversion and liquidity-sweep candidates. It applies taker fees, spread, both-side slippage and latency, and requires gross movement to exceed costs by a configurable multiple.

RR research evaluates 1R through 3R reachability. If both target and stop were reachable without ordered events, the sample is marked ambiguous and excluded. Exit policies that require ordered price paths remain `INSUFFICIENT_ORDERED_PATH_DATA`; no OHLC ordering is invented.

## Persistence and rollback

New tables are additive:

- `research_feature_vectors`
- `research_findings`
- `research_hypotheses`
- `research_hypothesis_evaluations`
- `research_model_runs`
- `research_strategy_recommendations`

Older application versions ignore them. Rolling application code back does not require deleting research data.

## Telegram

- `/edge_discovery`
- `/feature_edge`
- `/hypotheses`
- `/forward_tests`
- `/rr_research`
- `/exit_research`
- `/confidence_research`
- `/portfolio_edge`

Existing `/edge_report`, `/strategy_lab`, `/strategy_compare`, `/regimes`, `/signal_rankings` and `/scalping_research` remain available.

## AI certification correction

`AI_CERTIFICATION_MAX_TOKENS` remains a required bounded integer. The supported range is corrected to 128–4096; `192` is valid. Certification remains an explicit paid operator action and is never called automatically.


## v9.9.7 — Unified Lifecycle Authority

## v9.9.5a — Paper Execution Engine Foundation

New paper executions now use `paper_execution_positions` as their durable
lifecycle authority. Automatic opens run through `CopyExecutionEngine`; signal
TP and terminal transitions are applied exactly once through a persistent
position-lifecycle event ledger. `paper_positions` remains populated as a
temporary compatibility projection for rollback and legacy analytics.

See `V9_9_7_UNIFIED_LIFECYCLE_AUTHORITY.md`.

Approved deterministic copy plans can now pass through an idempotent `CopyExecutionEngine`. The engine reserves the plan in the persistent execution journal, atomically claims it, invokes the paper adapter, and records `EXECUTED` or `FAILED` as a terminal result. Duplicate calls return the existing journal outcome without executing twice. LIVE adapters remain blocked fail-closed.


## v9.9.4 Copy Execution Planning Layer

### v9.9.3 foundation

Signals are now converted into deterministic, side-effect-free execution plans before any executor acts. Each plan contains validated sizing, leverage, SL/TP, risk metadata, a copy-profile snapshot, and an idempotency key. Rejections are formal plans too, with stable codes such as `MAX_POSITIONS` or `AUTO_COPY_DISABLED`. LIVE execution remains off.

Each Telegram user can connect an isolated BingX/OKX account. Credentials are encrypted before PostgreSQL storage and authenticated commands resolve them by `telegram_user_id`; user orders never fall back to the bot owner's API key. See `V9_9_0_MULTI_USER_EXCHANGE_ACCOUNTS.md`.

# LiquidityVisionBot v9.9.17

Telegram trading-intelligence system for market analysis, watchlists, signal lifecycle tracking, trade management, research, and adaptive decision support.


### Copy Trading Profile foundation

The per-user copy product supports risk, fixed-USDT, equity-percent and trusted-source proportional sizing, named risk profiles, limits and filters. Automatic PAPER copy runs through the durable execution queue and unified lifecycle; LIVE execution remains independently fail-closed.

## v9.8.8 — Authenticated Safety Core

- Captures authenticated balances, positions, and open orders through one fail-closed `ExchangeManager` snapshot.
- Adds `/exchange_account`, `/exchange_safety`, and `/exchange_preflight`.
- Validates proposed order intent against demo/live mode, symbol whitelist, notional, leverage, open-position limits, duplicate orders, and exchange tick/step rules.
- Keeps LIVE execution globally locked and preserves the no-write adapter contract.
- Combines the planned authenticated-read milestone and execution-safety milestone without skipping validation.

## v9.8.3 — Exchange Reachability: OKX Read-Only

- Adds a read-only Bybit V5 linear-contract adapter alongside Binance USD-M.
- Classifies connectivity as CONNECTED, PUBLIC ONLY, NOT CONFIGURED, GEO BLOCKED, AUTH FAILED, or UNAVAILABLE.
- Exchange commands accept an optional exchange name; `EXCHANGE_DEFAULT=okx` selects the default.
- OKX accepts both `BTCUSDT` and native `BTC-USDT-SWAP` instrument IDs.
- Demo Trading uses the official OKX REST endpoint with `x-simulated-trading: 1`.
- `/exchanges` diagnoses each adapter independently and safely displays the public endpoint.
- LIVE order submission remains unavailable by contract.

## v9.8.0 — Strategy Genome & Similar Trade Intelligence

Every paper execution attempt now stores an immutable Strategy Genome built from the full available signal context: structure, liquidity, BOS/CHOCH, FVG/OB, regime, session, volatility, execution quality and indicator state. `/copy_similar [signal_id]` searches resolved executed and zero-exposure shadow trades, then reports Win Rate, average R, MFE, MAE and closest Replay IDs.

## v9.5.0 — Guardrail Outcome Intelligence

Rejected copy attempts are now tracked through terminal lifecycle states with zero exposure, allowing the platform to quantify losses avoided and profitable trades missed by each guardrail. Use `/copy_guardrails`.

## v9.4.0 — Execution Intelligence & Rejection Analytics

The paper executor now exposes a 30-day decision funnel, ranked rejection reasons, rejected symbols and timeframes, and recent rejected attempts through `/copy_rejections`. Analytics remain read-only and LIVE execution remains disabled.

## Release notes — v9.3.0

- Added dependency-resilient EMA, RSI, and MACD implementations.
- Restored the missing legacy Brain contract through the current Decision Brain.
- Added centralized release-integrity validation and synchronized release metadata.
- Added regression coverage for runtime imports and minimal production images.

## Core lifecycle

`analysis → observation → trade plan → triggered → active → TP/stop/invalidation → replay/research`

The bot keeps the original plan immutable after activation, records signal events, calculates MFE/MAE and realized R, and persists state in PostgreSQL on Render.

## Production components

- Telegram webhook or local polling runtime
- Binance/OKX-compatible market analysis services
- Watch Engine, Observation Monitor, and Signal Tracker
- Persistent signal lifecycle and trade replay
- Market regime, probability, adaptive weights, and loss forensics
- Runtime diagnostics, worker heartbeat, lifecycle integrity checks
- `/health`, `/healthz`, `/admin_status`
- `tools/smoke_test.py`

## Required environment variables

```env
BOT_TOKEN=...
BOT_MODE=webhook
DATABASE_URL=postgresql://...
WEBHOOK_BASE_URL=https://your-service.onrender.com
MONITOR_CRON_SECRET=...
ADMIN_IDS=123456789
REQUIRE_PERSISTENT_DB=true
PGSSLMODE=require
PYTHON_VERSION=3.12.10
APP_VERSION=9.9.17
SCHEMA_VERSION=1
LOG_LEVEL=INFO
```

Use the **Internal Database URL** when both the web service and Render PostgreSQL belong to the same Render workspace.

## Local start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

On Windows activate with `.venv\\Scripts\\activate`.

## Deployment

Render start command:

```bash
python bot.py
```

Health endpoint:

```text
GET /health
```

The protected monitor endpoint can be called by an external cron on Render Free:

```text
GET /internal/monitor?token=<MONITOR_CRON_SECRET>
```

## Diagnostics

Set your Telegram numeric ID in `ADMIN_IDS`, then run:

```text
/admin_status
```

The report shows database latency, worker freshness, active lifecycle counts, watch errors, duplicate open plans, and invalid active records.

After deployment run:

```bash
python tools/smoke_test.py
```

A non-zero exit code means the database or lifecycle integrity check failed.

## Tests

```bash
pytest -q
```

## Architecture

- `handlers/` — Telegram commands and buttons
- `services/` — orchestration, market access, monitoring, reports
- `core/` — analysis and decision engines
- `database/` — schema, persistence, lifecycle history
- `domain/` — typed market/trade entities
- `tools/` — operational and research scripts
- `tests/` — regression and lifecycle tests

## Release notes — v7.2.0

- Added centralized runtime diagnostics.
- Added worker stale-state detection.
- Added `/admin_status`.
- Expanded `/health` with counts and lifecycle integrity.
- Added schema migration registry foundation.
- Added production smoke test.
- Cleaned duplicate nested source tree and embedded Git metadata from release archive.


## v9.8.8 Autonomous Demo Execution

See `V9_8_8_AUTONOMOUS_DEMO_EXECUTION.md`. BingX demo orders can be submitted automatically when `DEMO_EXECUTION_ENABLED=true`; live writes remain blocked.


## v9.9.4
Persistent execution journal and idempotency foundation are documented in `V9_9_4_EXECUTION_JOURNAL_IDEMPOTENCY.md`.
# Unified portfolio accounting (v9.9.8)

Paper portfolio accounting is derived from unified execution positions and the idempotent portfolio ledger. Set `PORTFOLIO_ACCOUNTING_SOURCE=SHADOW` (default), `UNIFIED`, or `LEGACY`. SHADOW is the safe rollout mode; UNIFIED fails closed whenever an economically open position has missing or invalid risk metadata. See `V9_9_8_UNIFIED_PORTFOLIO_ACCOUNTING.md` for formulas, migration, and rollback details.

## Historical provenance migration (v9.9.9)

Startup incrementally catalogs legacy paper executions in a normalized provenance model. It never fabricates orders, fills, fees, prices, risk, or lifecycle events. Analytics use unified outcomes first, truthfully reconstructable unlinked history second, and an explicit legacy compatibility path until catalog coverage completes. See `V9_9_9_PROVENANCE_MIGRATION.md`.
# LiquidityVisionBot v9.9.11

The durable live-execution foundation adds normalized adapter capabilities, stable exchange identities, persistent attempts/fills, restart recovery and fail-closed readiness controls. Real-money execution remains disabled by default; see `V9_9_10_DURABLE_LIVE_EXECUTION.md` before deployment.

v9.9.11 certifies the existing BingX USDT-M perpetual adapter for real authenticated `LIVE_DRY_RUN` and controlled VST economic certification. Production LIVE remains fail-closed. See `V9_9_11_BINGX_PRODUCTION_ADAPTER.md`.

The final v9.9.11 stabilization invariants, deployment notes, remaining constraints, and staged GPT-trading integration plan are documented in `V9_9_11_PRODUCTION_HARDENING.md`.

# LiquidityVisionBot v9.9.12

v9.9.12 adds a provider-neutral GPT trading intelligence layer in `AI_SHADOW` mode. It records structured advisory recommendations, evidence, uncertainty, cost, calibration and outcomes without changing deterministic admission, sizing, lifecycle, portfolio, or exchange execution. `AI_PROVIDER=disabled` is fully functional and records deterministic abstentions. See `V9_9_12_GPT_TRADING_INTELLIGENCE.md`.

# LiquidityVisionBot v9.9.14

v9.9.14 adds expiring provider certification, governance audit events, a durable global AI kill switch, rolling provider-quality telemetry, drift detection, deterministic shadow experiments, counterfactual cohorts and stricter startup validation. The AI layer remains advisory and `AI_GATED` remains blocked. See `V9_9_14_AI_PROVIDER_CERTIFICATION.md`.

# LiquidityVisionBot v9.9.15

v9.9.15 normalizes Chat Completions and Responses structured output before one shared validation pipeline, makes real-provider certification bounded and repeat-safe, and scopes governance evidence to the exact provider/model/prompt/schema identity. Historical disabled and unscoped decisions remain immutable and cannot qualify a current provider. The recommended observation transport is OpenAI Responses with `gpt-5.6-terra`, strict JSON Schema, low reasoning effort, external versioned pricing, one provider attempt, and `AI_GATED` still blocked. See `V9_9_15_OPENAI_PROVIDER_CERTIFICATION.md` and `OPENAI_PROVIDER_RUNBOOK.md`.

# LiquidityVisionBot v9.9.16

v9.9.16 completes automatic multi-user PAPER copy from signal eligibility through deterministic policy, sizing, durable queue, order/fill, unified TP/SL lifecycle, accounting and restart recovery. It adds immutable decision-time research snapshots, append-only outcomes, four versioned non-trading Strategy Lab baselines, overlapping market regimes, diagnostic rankings, descriptive cohorts, and after-cost scalping research. Responses extraction now handles reasoning-first and reordered output, message content arrays, SDK `output_text`/`output_parsed`, structured parsed parts, refusals and incomplete reasons without storing provider reasoning. See `V9_9_16_PRODUCTION_COPY_RESEARCH.md`.

# LiquidityVisionBot v9.9.17

v9.9.17 adds a leakage-controlled Edge Discovery engine over immutable decision-time snapshots: versioned normalized features, clean market/policy/execution/intervention outcomes, conservative sample tiers, reproducible bootstrap intervals, controlled feature comparisons, bounded combination mining, negative-edge candidates, chronological walk-forward evaluation, frozen hypotheses and genuine forward cohorts. Strategy selection, signal ranking, scalping, RR, exit, portfolio and AI comparisons remain research-only and have no execution authority. See `V9_9_17_EDGE_DISCOVERY.md` and `RESEARCH_RUNBOOK.md`.

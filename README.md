# Liquidity Vision Intelligence v9.8.1

Telegram trading-intelligence system for market analysis, watchlists, signal lifecycle tracking, trade management, research, and adaptive decision support.


## v9.8.1 — Exchange Foundation: Binance Read-Only

The platform now has a typed async exchange boundary and a working Binance USD-M Futures read-only adapter. `/exchanges` checks public reachability and authenticated access; balance, positions, open orders and symbol rules can be inspected without exposing credentials. No LIVE order method exists yet, so Paper Copy Trading remains the only execution path.

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
APP_VERSION=9.8.1
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

# LiquidityVisionBot v10.1.0 — Intelligence Product Platform

This release adds a deterministic AI Context Compiler, explicit production
microstructure configuration diagnostics, categorized command discovery, a
Telegram command menu, versioned Free/Pro/Elite entitlements, preferences,
smart watchlist controls, bounded alert eligibility, a strategy-aware scanner,
PAPER copy performance separation, and measured/audited operations.

## Production configuration

Set `MICROSTRUCTURE_COLLECTION_ENABLED=true` in the effective Render service
environment. The observer uses credential-free BingX public futures market
endpoints, persists only bounded aggregates, and has no order authority. A
Render Dashboard variable overrides a Blueprint value; after synchronization,
verify `/system_health`, `/data_health BTCUSDT`, `/orderbook BTCUSDT`,
`/funding BTCUSDT`, and `/open_interest BTCUSDT`.

Keep `LIVE_TRADING_ENABLED=false` and `AI_GATED_ENABLED=false`. AI remains
advisory; research and entitlements cannot grant execution authority.

## Deployment procedure

1. Review the unstaged diff and run the release gate.
2. Set `APP_VERSION=10.1.0` and the microstructure/retention variables from
   `render.yaml` in the effective Render environment.
3. Commit and push through the normal reviewed workflow (not performed here).
4. Deploy the Render Blueprint/service and verify the startup phase timings,
   database migration, worker health, and webhook readiness.
5. Run the Telegram verification commands listed above plus `/help`,
   `/scanner`, `/watchlist`, `/plans`, `/my_plan`, `/ai_failures`, and
   `/ai_abstentions`.

Telegram Stars remains the only existing payment boundary. No card/payment
data is stored, and manual grants are operator-only and audited.

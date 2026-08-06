# LiquidityVisionBot v9.9.11 — BingX Production Adapter Certification

## Supported API and environments

The adapter uses BingX USDT-M perpetual Swap API v2/v3. `BINGX_DEMO=true` selects the explicit Production Simulated environment (`prod-vst`, `open-api-vst.bingx.com`); `false` selects Production Live (`prod-live`, `open-api.bingx.com`). The environment is never inferred from connectivity or account contents.

Supported account modes are one-way (`BOTH`) and hedge (`LONG`/`SHORT`). The adapter detects the current mode and never changes it automatically. Hedge closes require an unambiguous matching position and the correct closing side. One-way closes use `reduceOnly=true`; hedge closes use BingX position-side semantics because BingX rejects the `reduceOnly` parameter in hedge mode.

## Permissions and secrets

The API key requires perpetual-futures account read and futures trading permissions. Withdrawal permission is not required and should remain disabled. BingX does not expose withdrawal scope through the selected Swap endpoints, so diagnostics report it as unknown/not required.

Credentials continue through the existing encrypted per-user credential mechanism or environment configuration. Certification reports, live-account rows, request checksums and logs contain no key, secret, signature or raw payload. Parameters reject signing-delimiter and newline characters.

## Signing and transport

Authenticated requests sort parameters, URL-encode them, sign the exact encoded string with HMAC-SHA256, and use `X-BX-APIKEY`. POST requests use a form-encoded body. GET/DELETE use the signed query string. Only idempotent reads retry; economic POST and DELETE calls are single-dispatch. Timestamp errors resynchronize against BingX server time before retrying reads. Connections use the adapter's shared pooled `aiohttp` session, bounded concurrency, connect/read deadlines and jittered read backoff.

## Certification runbook

1. Configure a BingX VST API key with futures read/trade permissions and no withdrawal permission.
2. Set `BINGX_DEMO=true`, `EXECUTION_MODE=LIVE_DRY_RUN`, `LIVE_EXECUTION_ENABLED=false`.
3. Run `/live_sync bingx BTCUSDT` and `/live_certify bingx BTCUSDT 0.001 60000` in private chat. These perform authenticated reads and precision validation with exactly zero order submissions.
4. Inspect `/live_account bingx` and `/live_readiness bingx`. A dry-run report deliberately shows `ECONOMIC_VST_CERTIFICATION_REQUIRED`.
5. For controlled VST economic certification only, set `BINGX_VST_CERTIFICATION_ENABLED=true`, verify the configured quantity is tiny, and run `/live_certify bingx execute CERTIFY_VST` in private chat. This submits a VST market entry, verifies fills, submits a capped safe close, ingests fills, and passes only after zero exposure is confirmed.
6. Disable `BINGX_VST_CERTIFICATION_ENABLED` immediately after the run.

Economic certificates expire after `BINGX_CERTIFICATION_TTL_HOURS`. A read-only dry-run certificate never authorizes LIVE.

## Production rollout

Keep `LIVE_EXECUTION_ENABLED=false`, `BINGX_PRODUCTION_ADAPTER_ALLOWED=false`, `EXECUTION_MODE=PAPER`, and the account kill switch active during deployment. LIVE additionally requires a current matching VST economic certificate, synchronized account and time state, known account mode, resolved portfolio, limits, daily-loss protection, two-step confirmation, account enablement and explicit kill-switch release. No Telegram command performs all those actions.

## Rollback

Set `LIVE_EXECUTION_ENABLED=false`, `BINGX_PRODUCTION_ADAPTER_ALLOWED=false`, `BINGX_VST_CERTIFICATION_ENABLED=false`, and `EXECUTION_MODE=PAPER` or `DISABLED`. Run `/live_disable bingx`. Roll application code back to v9.9.10 while retaining the additive certification and cache tables for audit. Establish exchange truth before changing any `UNKNOWN` or `RECOVERY_REQUIRED` execution.

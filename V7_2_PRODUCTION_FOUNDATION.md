# v7.2.0 — Production Foundation

## Added
- Centralized runtime diagnostics service.
- `/admin_status` protected by `ADMIN_IDS`.
- Worker freshness and stale-heartbeat detection.
- Expanded webhook `/health` and `/healthz` payloads.
- Lifecycle integrity checks for duplicate plans and malformed active trades.
- Database `schema_migrations` registry foundation.
- `tools/smoke_test.py` for post-deploy validation.
- Production-focused README and environment documentation.

## Release cleanup
- Removed nested stale project copy.
- Removed embedded `.git` metadata.

## Render variables
Add `ADMIN_IDS=<your Telegram numeric ID>`, `APP_VERSION=7.2.0`, `SCHEMA_VERSION=1`, and optionally `WORKER_STALE_AFTER=900`.

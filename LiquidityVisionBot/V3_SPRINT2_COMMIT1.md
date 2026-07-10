# V3 Sprint 2 — Commit 1: Resilient Background Monitor

- Atomic SQLite leases prevent duplicate processing during Render deploy overlap.
- Compare-and-set lifecycle transitions prevent duplicate events and notifications.
- Per-signal retry backoff and monitor error diagnostics.
- Bounded concurrent market requests.
- Persistent monitor run telemetry.
- Highest TP is evaluated first to handle fast candles correctly.
- Graceful shutdown and configurable interval/batch/concurrency.

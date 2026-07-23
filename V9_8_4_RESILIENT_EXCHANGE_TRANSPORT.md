# v9.8.4 — Resilient Exchange Transport

This release hardens the existing OKX read-only adapter for unstable production networking. It introduces bounded retries with exponential backoff, separate connect/read deadlines, typed transient failures, and a TTL cache for public symbol rules. Permanent authentication and configuration failures remain fail-fast. No order write capability is present.

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class RetryDecision:
    retryable: bool
    delay_seconds: int
    code: str


class ExecutionRetryPolicy:
    """Deterministic retry policy for transient paper execution failures."""

    TRANSIENT_CODES = {
        "ADAPTER_EXCEPTION", "TIMEOUT", "NETWORK_ERROR", "RATE_LIMIT",
        "TEMPORARY_UNAVAILABLE", "LEASE_EXPIRED",
    }

    def __init__(self, *, base_seconds: int | None = None, max_seconds: int | None = None):
        self.base_seconds = max(1, base_seconds or int(os.getenv("COPY_RETRY_BASE_SECONDS", "30")))
        self.max_seconds = max(self.base_seconds, max_seconds or int(os.getenv("COPY_RETRY_MAX_SECONDS", "900")))

    def decide(self, *, code: str, attempt_count: int, max_attempts: int) -> RetryDecision:
        if attempt_count >= max_attempts:
            return RetryDecision(False, 0, "MAX_ATTEMPTS")
        if code not in self.TRANSIENT_CODES:
            return RetryDecision(False, 0, "NON_RETRYABLE")
        delay = min(self.max_seconds, int(self.base_seconds * math.pow(2, max(0, attempt_count - 1))))
        return RetryDecision(True, delay, "RETRY_SCHEDULED")

    @staticmethod
    def due_at(delay_seconds: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=max(0, delay_seconds))).isoformat()

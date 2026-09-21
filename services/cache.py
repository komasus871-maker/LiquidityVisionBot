from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Any


class Cache:
    """Small process-local TTL/LRU cache for immutable provider snapshots."""

    def __init__(self, max_entries: int | None = None) -> None:
        configured = max_entries if max_entries is not None else int(
            os.getenv("MARKET_CACHE_MAX_ENTRIES", "256")
        )
        self.max_entries = max(8, min(int(configured), 2_000))
        self.cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock = threading.RLock()

    def _prune(self, now: float) -> None:
        expired = [key for key, (_, expires) in self.cache.items() if now > expires]
        for key in expired:
            self.cache.pop(key, None)
        while len(self.cache) > self.max_entries:
            self.cache.popitem(last=False)

    def get(self, key: str) -> Any | None:
        now = time.time()
        with self._lock:
            item = self.cache.get(key)
            if item is None:
                return None
            value, expires = item
            if now > expires:
                self.cache.pop(key, None)
                return None
            self.cache.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: float = 20) -> None:
        now = time.time()
        with self._lock:
            self.cache[key] = (value, now + max(0.01, float(ttl)))
            self.cache.move_to_end(key)
            self._prune(now)


cache = Cache()

"""Thread-safe bounded cache for shared market analysis."""
from __future__ import annotations

import copy
import hashlib
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class CacheEntry:
    value: Any
    created_at: float


class AnalysisCache:
    def __init__(self, ttl_seconds: float | None = None, max_entries: int | None = None):
        self.ttl_seconds = float(ttl_seconds or os.getenv("ANALYSIS_CACHE_TTL", "45"))
        self.max_entries = int(max_entries or os.getenv("ANALYSIS_CACHE_SIZE", "256"))
        self._items: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def fingerprint(dataframe: Any, namespace: str = "analysis") -> str:
        hasher = hashlib.blake2b(digest_size=16)
        hasher.update(namespace.encode("utf-8"))
        try:
            hasher.update(str(len(dataframe)).encode())
            columns = [c for c in ("open", "high", "low", "close", "volume", "confirm") if c in dataframe.columns]
            hasher.update("|".join(columns).encode())
            sample = dataframe[columns].tail(8)
            # Stable enough for candle frames and much cheaper than hashing full history.
            hasher.update(sample.to_csv(index=True, header=False).encode("utf-8"))
            attrs = getattr(dataframe, "attrs", {}) or {}
            for key in ("symbol", "timeframe", "interval", "source"):
                if key in attrs:
                    hasher.update(f"{key}={attrs[key]}".encode("utf-8"))
        except Exception:
            hasher.update(repr(dataframe).encode("utf-8", errors="replace"))
        return hasher.hexdigest()

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                self.misses += 1
                return None
            if now - entry.created_at > self.ttl_seconds:
                self._items.pop(key, None)
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return copy.deepcopy(entry.value)

    def set(self, key: str, value: Any) -> Any:
        with self._lock:
            self._items[key] = CacheEntry(copy.deepcopy(value), time.monotonic())
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)
        return value

    def get_or_compute(self, key: str, factory: Callable[[], Any]) -> tuple[Any, bool]:
        cached = self.get(key)
        if cached is not None:
            return cached, True
        value = factory()
        self.set(key, value)
        return value, False

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def stats(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "entries": len(self._items),
                "hits": self.hits,
                "misses": self.misses,
                "ttl_seconds": self.ttl_seconds,
                "max_entries": self.max_entries,
            }


analysis_cache = AnalysisCache()

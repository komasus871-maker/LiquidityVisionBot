"""Canonical analysis context shared by all analysis consumers.

The context is intentionally transport-friendly: consumers can use the typed
object internally while legacy handlers continue receiving ordinary dicts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(slots=True)
class AnalysisIdentity:
    symbol: str | None = None
    timeframe: str | None = None
    source: str = "market"
    fingerprint: str | None = None


@dataclass(slots=True)
class AnalysisContext:
    dataframe: Any
    identity: AnalysisIdentity = field(default_factory=AnalysisIdentity)
    raw: dict[str, Any] = field(default_factory=dict)
    market: dict[str, Any] = field(default_factory=dict)
    structure: dict[str, Any] = field(default_factory=dict)
    liquidity: dict[str, Any] = field(default_factory=dict)
    volume: dict[str, Any] = field(default_factory=dict)
    momentum: dict[str, Any] = field(default_factory=dict)
    regime: dict[str, Any] = field(default_factory=dict)
    trade_dna: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def publish(self, section: str, values: Mapping[str, Any]) -> None:
        target = getattr(self, section)
        if not isinstance(target, dict):
            raise TypeError(f"AnalysisContext section {section!r} is not a mapping")
        target.update(values)
        self.raw.update(values)

    def snapshot(self) -> dict[str, Any]:
        """Return compact metadata safe to attach to legacy output."""
        return {
            "symbol": self.identity.symbol,
            "timeframe": self.identity.timeframe,
            "source": self.identity.source,
            "fingerprint": self.identity.fingerprint,
            "created_at": self.created_at.isoformat(),
            "stages": list(self.diagnostics.get("completed_stages", ())),
            "cache_hit": bool(self.diagnostics.get("cache_hit", False)),
            "pipeline_version": "7.6",
        }

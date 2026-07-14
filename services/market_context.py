from __future__ import annotations

from typing import Any


class MarketContextEngine:
    """Adds a conservative BTC context layer for altcoin decisions.

    This is deliberately rule based. It never invents TOTAL or dominance data
    when the runtime provider does not expose those instruments. BTC is treated
    as the broad risk anchor and can only reduce execution quality when it
    conflicts; it cannot manufacture a strong signal by itself.
    """

    @staticmethod
    def _direction(data: dict[str, Any] | None) -> str:
        return str((data or {}).get("direction") or "NEUTRAL").upper()

    @staticmethod
    def _score(data: dict[str, Any] | None) -> float:
        return float((data or {}).get("direction_score") or 0)

    def enrich(self, analysis: dict[str, Any], *, symbol: str, btc: dict[str, Any] | None = None) -> dict[str, Any]:
        data = dict(analysis)
        symbol = symbol.upper()
        if symbol == "BTC" or not btc:
            data["global_context"] = {
                "available": symbol == "BTC",
                "anchor": "BTC",
                "alignment": "SELF" if symbol == "BTC" else "UNAVAILABLE",
                "risk_multiplier": 1.0,
                "summary": "BTC is the market anchor." if symbol == "BTC" else "BTC context unavailable; no adjustment applied.",
            }
            return data

        side = self._direction(data)
        btc_side = self._direction(btc)
        btc_score = self._score(btc)
        btc_regime = str((btc.get("market_regime") or {}).get("code") or "UNKNOWN")
        strong_btc = btc_score >= 62 and btc_side in {"LONG", "SHORT"}
        aligned = strong_btc and side == btc_side
        conflict = strong_btc and side in {"LONG", "SHORT"} and side != btc_side

        multiplier = 1.0
        adjustment = 0.0
        if conflict:
            multiplier = 0.65
            adjustment = -10.0
            summary = f"BTC context conflicts: BTC favors {btc_side} ({btc_score:.0f}/100)."
        elif aligned:
            multiplier = 1.05
            adjustment = 3.0
            summary = f"BTC context aligns: BTC also favors {btc_side} ({btc_score:.0f}/100)."
        else:
            multiplier = 0.85 if btc_regime in {"RANGING", "COMPRESSION", "TRANSITION"} else 0.95
            adjustment = -4.0 if multiplier < 0.9 else -2.0
            summary = f"BTC context is not decisive ({btc_side}, {btc_score:.0f}/100; {btc_regime})."

        setup = max(0.0, min(100.0, float(data.get("setup_score", data.get("score")) or 0) + adjustment))
        readiness = max(0.0, min(100.0, float(data.get("execution_readiness") or 0) + adjustment))
        data["setup_score"] = setup
        data["score"] = setup
        data["execution_readiness"] = readiness
        data["readiness"] = readiness
        data["global_context"] = {
            "available": True,
            "anchor": "BTC",
            "btc_direction": btc_side,
            "btc_score": btc_score,
            "btc_regime": btc_regime,
            "alignment": "ALIGNED" if aligned else "CONFLICT" if conflict else "MIXED",
            "risk_multiplier": multiplier,
            "score_adjustment": adjustment,
            "summary": summary,
        }
        if conflict:
            reasons = list(data.get("reasons") or [])
            reasons.append(f"⚠️ BTC context conflicts with {side}")
            data["reasons"] = reasons
            triggers = list(data.get("triggers") or [])
            triggers.append("Wait for BTC context to stop conflicting")
            data["triggers"] = triggers
        return data

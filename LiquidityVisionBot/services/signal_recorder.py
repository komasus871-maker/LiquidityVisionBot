from typing import Any
from database.signal_history import SignalHistory
from services.premium import PremiumService


class SignalRecorder:
    def __init__(self, history: SignalHistory | None = None):
        self.history = history or SignalHistory()
        self.premium = PremiumService()

    @staticmethod
    def _setup_key(analysis: dict[str, Any]) -> str:
        parts = [analysis.get("structure"), analysis.get("choch"), analysis.get("sweep"), analysis.get("order_block"), analysis.get("premium", {}).get("zone")]
        return " | ".join(str(p).replace("✅", "").strip() for p in parts if p)

    def record(self, *, symbol: str, timeframe: str, analysis: dict[str, Any], owner_telegram_id: int | None = None,
               notification_chat_id: int | None = None, min_confidence: float = 54) -> int | None:
        executable = {"🟢 READY", "🟡 WAIT FOR TRIGGER", "🎯 WAIT FOR PULLBACK", "🔄 REVERSAL WATCH"}
        if analysis.get("execution_status") not in executable:
            return None
        if analysis.get("confidence", 0) < min_confidence or analysis.get("rr", 0) < 1.35:
            return None

        if owner_telegram_id is not None:
            stats = self.history.get_stats(owner_telegram_id)
            open_count = (stats.get("watching_count") or 0) + (stats.get("triggered_count") or 0) + (stats.get("active_count") or 0)
            limit = 200 if self.premium.status(owner_telegram_id)["active"] else 20
            if open_count >= limit:
                return None

        status = "ACTIVE" if analysis.get("execution_status") == "🟢 READY" else "WATCHING"
        features = {key: analysis.get(key) for key in (
            "trend", "structure", "bos", "choch", "liquidity", "sweep", "order_block", "breaker",
            "mitigation", "fvg", "premium", "volume", "displacement", "rsi", "macd", "ema50", "ema200",
            "market_bias", "execution_status", "triggers", "direction_score", "entry_quality", "risk_quality",
            "execution_readiness", "opportunity_category"
        )}
        payload = {
            "owner_telegram_id": owner_telegram_id, "notification_chat_id": notification_chat_id,
            "symbol": symbol.upper(), "timeframe": timeframe, "side": analysis.get("direction", "LONG"),
            "entry": analysis["entry"], "preferred_entry_low": analysis.get("preferred_entry_low"),
            "preferred_entry_high": analysis.get("preferred_entry_high"), "stop": analysis["stop"],
            "tp1": analysis["tp1"], "tp2": analysis["tp2"], "tp3": analysis["tp3"], "rr": analysis["rr"],
            "confidence": analysis["confidence"], "bull_score": analysis["bull_score"], "bear_score": analysis["bear_score"],
            "recommendation": analysis["recommendation"], "setup_key": self._setup_key(analysis),
            "features": features, "reasons": analysis["reasons"], "status": status,
        }
        duplicate = self.history.find_duplicate(owner_telegram_id, payload["symbol"], timeframe, payload["side"])
        if duplicate:
            return self.history.refresh_duplicate(duplicate["id"], payload)
        return self.history.save(payload)

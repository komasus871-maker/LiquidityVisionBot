from typing import Any
from database.signal_history import SignalHistory


class SignalRecorder:
    def __init__(self, history: SignalHistory | None = None):
        self.history = history or SignalHistory()

    @staticmethod
    def _side(analysis: dict[str, Any]) -> str:
        return analysis.get("direction", "LONG")

    @staticmethod
    def _setup_key(analysis: dict[str, Any]) -> str:
        parts = [analysis.get("structure"), analysis.get("choch"), analysis.get("sweep"), analysis.get("order_block"), analysis.get("premium", {}).get("zone")]
        return " | ".join(str(p).replace("✅", "").strip() for p in parts if p)

    def record(self, *, symbol: str, timeframe: str, analysis: dict[str, Any], min_confidence: float = 55) -> int | None:
        # Record executable and conditional trade ideas; pure bias/watchlist observations are not trades.
        if analysis.get("execution_status") not in {"🟢 READY", "🟡 WAIT FOR TRIGGER"}:
            return None
        if analysis.get("confidence", 0) < min_confidence or analysis.get("rr", 0) < 1.35:
            return None

        features = {key: analysis.get(key) for key in (
            "trend", "structure", "bos", "choch", "liquidity", "sweep", "order_block",
            "breaker", "mitigation", "fvg", "premium", "volume", "displacement", "rsi",
            "macd", "ema50", "ema200", "market_bias", "execution_status", "triggers"
        )}
        return self.history.save({
            "symbol": symbol.upper(), "timeframe": timeframe, "side": self._side(analysis),
            "entry": analysis["entry"], "stop": analysis["stop"], "tp1": analysis["tp1"],
            "tp2": analysis["tp2"], "tp3": analysis["tp3"], "rr": analysis["rr"],
            "confidence": analysis["confidence"], "bull_score": analysis["bull_score"],
            "bear_score": analysis["bear_score"], "recommendation": analysis["recommendation"],
            "setup_key": self._setup_key(analysis), "features": features, "reasons": analysis["reasons"],
            "status": "ACTIVE" if analysis.get("execution_status") == "🟢 READY" else "WATCHING",
        })

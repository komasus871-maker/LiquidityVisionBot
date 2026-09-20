"""Reusable stages of the v7.6 unified analysis pipeline."""
from __future__ import annotations

from typing import Any

from utils.indicators import ema, rsi, macd
from utils.structure import Structure
from utils.choch import CHOCH
from utils.liquidity import Liquidity
from utils.sweep import Sweep
from utils.order_blocks import OrderBlocks
from utils.breaker_block import BreakerBlock
from utils.mitigation_block import MitigationBlock
from utils.fvg import FVG
from utils.premium_discount import PremiumDiscount
from utils.volume_profile import VolumeProfile
from utils.displacement import Displacement
from utils.atr import ATR
from services.market_regime import MarketRegimeEngine

from .context import AnalysisContext


class MarketService:
    name = "market"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        df = context.dataframe
        close = float(df["close"].iloc[-1])
        cached = (getattr(df, "attrs", {}) or {}).get("_lv_research_causal_cache") == "causal-prefix-features-v1"
        ema50 = float(df["__lv_ema50"].iloc[-1]) if cached else float(ema(df, 50).iloc[-1])
        ema200 = float(df["__lv_ema200"].iloc[-1]) if cached else float(ema(df, 200).iloc[-1])
        result = {
            "price": close,
            "ema50": ema50,
            "ema200": ema200,
            "trend": "🟢 Bullish" if ema50 > ema200 else "🔴 Bearish",
        }
        context.publish("market", result)
        return result


class StructureService:
    name = "structure"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        df = context.dataframe
        cached = (getattr(df, "attrs", {}) or {}).get("_lv_research_causal_cache") == "causal-prefix-features-v1"
        structure = Structure(df)
        result = {
            "structure": df["__lv_structure"].iloc[-1] if cached else structure.market_structure(),
            "bos": df["__lv_bos"].iloc[-1] if cached else structure.bos(),
            "choch": CHOCH(df).analyze(),
        }
        context.publish("structure", result)
        return result


class LiquidityService:
    name = "liquidity"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        df = context.dataframe
        cached = (getattr(df, "attrs", {}) or {}).get("_lv_research_causal_cache") == "causal-prefix-features-v1"
        result = {
            "liquidity": Liquidity(df).analyze(),
            "sweep": df["__lv_sweep"].iloc[-1] if cached else Sweep(df).analyze(),
            "order_block": OrderBlocks(df).analyze(),
            "breaker": BreakerBlock(df).analyze(),
            "mitigation": MitigationBlock(df).analyze(),
            "fvg": df["__lv_fvg"].iloc[-1] if cached else FVG(df).analyze(),
            "premium": df["__lv_premium"].iloc[-1] if cached else PremiumDiscount(df).analyze(),
        }
        context.publish("liquidity", result)
        return result


class VolumeService:
    name = "volume"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        frame = context.dataframe
        cached = (getattr(frame, "attrs", {}) or {}).get("_lv_research_causal_cache") == "causal-prefix-features-v1"
        result = {"volume": frame["__lv_volume"].iloc[-1] if cached else VolumeProfile(frame).analyze()}
        context.publish("volume", result)
        return result


class MomentumService:
    name = "momentum"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        df = context.dataframe
        cached = (getattr(df, "attrs", {}) or {}).get("_lv_research_causal_cache") == "causal-prefix-features-v1"
        if cached:
            macd_now = float(df["__lv_macd_line"].iloc[-1])
            signal_now = float(df["__lv_macd_signal"].iloc[-1])
        else:
            macd_line, signal = macd(df)
            macd_now = float(macd_line.iloc[-1])
            signal_now = float(signal.iloc[-1])
        result = {
            "rsi": float(df["__lv_rsi"].iloc[-1]) if cached else float(rsi(df).iloc[-1]),
            "macd": "🟢 Bullish" if macd_now > signal_now else "🔴 Bearish",
            "macd_bullish": macd_now > signal_now,
            "displacement": df["__lv_displacement"].iloc[-1] if cached else Displacement(df).analyze(),
            "atr": df["__lv_atr"].iloc[-1] if cached else ATR(df).analyze(),
        }
        context.publish("momentum", result)
        return result


class RegimeService:
    name = "regime"

    def __init__(self, engine: MarketRegimeEngine | None = None):
        self.engine = engine or MarketRegimeEngine()

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        frame = context.dataframe
        cached = (getattr(frame, "attrs", {}) or {}).get("_lv_research_causal_cache") == "causal-prefix-features-v1"
        result = {"market_regime": (frame["__lv_market_regime"].iloc[-1]
                                    if cached else self.engine.analyze(frame))}
        context.publish("regime", result)
        return result


class TradeDNAFoundationService:
    """Build a stable feature envelope for downstream DNA/similarity layers."""
    name = "trade_dna"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        raw = context.raw
        regime = raw.get("market_regime") or {}
        result = {
            "trend": raw.get("trend"),
            "structure": raw.get("structure"),
            "bos": raw.get("bos"),
            "choch": raw.get("choch"),
            "liquidity": raw.get("liquidity"),
            "sweep": raw.get("sweep"),
            "premium": raw.get("premium"),
            "volume": raw.get("volume"),
            "displacement": raw.get("displacement"),
            "rsi": raw.get("rsi"),
            "regime": regime.get("code") if isinstance(regime, dict) else regime,
        }
        context.trade_dna.update(result)
        return result

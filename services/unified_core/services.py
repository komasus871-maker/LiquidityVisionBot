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
        ema50 = float(ema(df, 50).iloc[-1])
        ema200 = float(ema(df, 200).iloc[-1])
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
        structure = Structure(df)
        result = {
            "structure": structure.market_structure(),
            "bos": structure.bos(),
            "choch": CHOCH(df).analyze(),
        }
        context.publish("structure", result)
        return result


class LiquidityService:
    name = "liquidity"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        df = context.dataframe
        result = {
            "liquidity": Liquidity(df).analyze(),
            "sweep": Sweep(df).analyze(),
            "order_block": OrderBlocks(df).analyze(),
            "breaker": BreakerBlock(df).analyze(),
            "mitigation": MitigationBlock(df).analyze(),
            "fvg": FVG(df).analyze(),
            "premium": PremiumDiscount(df).analyze(),
        }
        context.publish("liquidity", result)
        return result


class VolumeService:
    name = "volume"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        result = {"volume": VolumeProfile(context.dataframe).analyze()}
        context.publish("volume", result)
        return result


class MomentumService:
    name = "momentum"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        df = context.dataframe
        macd_line, signal = macd(df)
        macd_now = float(macd_line.iloc[-1])
        signal_now = float(signal.iloc[-1])
        result = {
            "rsi": float(rsi(df).iloc[-1]),
            "macd": "🟢 Bullish" if macd_now > signal_now else "🔴 Bearish",
            "macd_bullish": macd_now > signal_now,
            "displacement": Displacement(df).analyze(),
            "atr": ATR(df).analyze(),
        }
        context.publish("momentum", result)
        return result


class RegimeService:
    name = "regime"

    def __init__(self, engine: MarketRegimeEngine | None = None):
        self.engine = engine or MarketRegimeEngine()

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        result = {"market_regime": self.engine.analyze(context.dataframe)}
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

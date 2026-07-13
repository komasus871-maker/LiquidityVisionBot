from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RegimeResult:
    code: str
    label: str
    confidence: float
    trend_strength: float
    efficiency: float
    volatility_percentile: float
    volatility_state: str
    direction: str
    execution_mode: str
    allows_trend_entry: bool
    risk_multiplier: float
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "confidence": round(self.confidence, 1),
            "trend_strength": round(self.trend_strength, 1),
            "efficiency": round(self.efficiency, 1),
            "volatility_percentile": round(self.volatility_percentile, 1),
            "volatility_state": self.volatility_state,
            "direction": self.direction,
            "execution_mode": self.execution_mode,
            "allows_trend_entry": self.allows_trend_entry,
            "risk_multiplier": round(self.risk_multiplier, 2),
            "reasons": list(self.reasons),
        }


class MarketRegimeEngine:
    """Classify whether current conditions favor trend-following execution.

    The engine intentionally uses price-path quality and volatility context rather
    than another directional oscillator. Its purpose is to stop otherwise strong
    directional scores from becoming executable in choppy, transitional or
    exhausted conditions.
    """

    LOOKBACK = 24
    SLOPE_LOOKBACK = 10

    @staticmethod
    def _true_range(df: pd.DataFrame) -> pd.Series:
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        close = pd.to_numeric(df["close"], errors="coerce")
        prev_close = close.shift(1)
        return pd.concat(
            [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)

    @staticmethod
    def _percentile_rank(series: pd.Series, value: float) -> float:
        clean = series.replace([np.inf, -np.inf], np.nan).dropna()
        if clean.empty:
            return 50.0
        return float((clean <= value).mean() * 100.0)

    def analyze(self, df: pd.DataFrame) -> dict[str, Any]:
        if df is None or len(df) < 80:
            return RegimeResult(
                code="UNKNOWN",
                label="⚪ Unknown / Insufficient Data",
                confidence=0.0,
                trend_strength=0.0,
                efficiency=0.0,
                volatility_percentile=50.0,
                volatility_state="NORMAL",
                direction="NEUTRAL",
                execution_mode="OBSERVE",
                allows_trend_entry=False,
                risk_multiplier=0.5,
                reasons=("Insufficient confirmed candles for regime classification",),
            ).as_dict()

        close = pd.to_numeric(df["close"], errors="coerce").astype(float)
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()
        tr = self._true_range(df)
        atr = tr.rolling(14, min_periods=5).mean()

        current_price = max(abs(float(close.iloc[-1])), 1e-12)
        current_atr = max(float(atr.iloc[-1]), current_price * 1e-8)

        window = close.iloc[-self.LOOKBACK :]
        path = float(window.diff().abs().sum())
        net = float(abs(window.iloc[-1] - window.iloc[0]))
        efficiency = 0.0 if path <= 1e-12 else min(1.0, net / path)

        ema_spread_atr = abs(float(ema50.iloc[-1] - ema200.iloc[-1])) / current_atr
        ema_slope_atr = abs(float(ema50.iloc[-1] - ema50.iloc[-self.SLOPE_LOOKBACK])) / current_atr
        trend_strength_raw = ema_spread_atr * 0.45 + ema_slope_atr * 0.35 + efficiency * 3.0 * 0.20
        trend_strength = min(100.0, trend_strength_raw / 2.25 * 100.0)

        atr_pct = atr / close.abs().replace(0, np.nan)
        vol_percentile = self._percentile_rank(atr_pct.iloc[-180:], float(atr_pct.iloc[-1]))
        if vol_percentile >= 85:
            volatility_state = "EXTREME"
        elif vol_percentile >= 68:
            volatility_state = "HIGH"
        elif vol_percentile <= 20:
            volatility_state = "COMPRESSED"
        else:
            volatility_state = "NORMAL"

        returns = close.pct_change().iloc[-self.LOOKBACK :].dropna()
        positive_share = float((returns > 0).mean()) if not returns.empty else 0.5
        direction_consistency = abs(positive_share - 0.5) * 2.0
        price_above = float(close.iloc[-1]) > float(ema50.iloc[-1]) > float(ema200.iloc[-1])
        price_below = float(close.iloc[-1]) < float(ema50.iloc[-1]) < float(ema200.iloc[-1])
        direction = "LONG" if price_above else "SHORT" if price_below else "NEUTRAL"

        reasons: list[str] = []
        if efficiency >= 0.48:
            reasons.append(f"Directional price-path efficiency is high ({efficiency * 100:.0f}%)")
        elif efficiency <= 0.25:
            reasons.append(f"Price path is choppy ({efficiency * 100:.0f}% efficiency)")
        if ema_spread_atr >= 1.0:
            reasons.append("EMA separation confirms directional expansion")
        elif ema_spread_atr <= 0.35:
            reasons.append("EMA compression suggests weak directional control")
        if volatility_state == "EXTREME":
            reasons.append("Volatility is at an extreme historical percentile")
        elif volatility_state == "COMPRESSED":
            reasons.append("Volatility is compressed and breakout risk is elevated")

        strong_trend = (
            direction != "NEUTRAL"
            and efficiency >= 0.38
            and ema_spread_atr >= 0.65
            and ema_slope_atr >= 0.45
        )
        choppy = efficiency <= 0.27 and ema_spread_atr <= 0.75
        compressed = volatility_state == "COMPRESSED" and ema_spread_atr <= 0.65
        extreme_expansion = volatility_state == "EXTREME" and (
            ema_slope_atr >= 1.25 or efficiency >= 0.58
        )

        if extreme_expansion and direction != "NEUTRAL":
            code = "VOLATILE_EXPANSION"
            label = "🟠 Volatile Expansion"
            execution_mode = "WAIT FOR PULLBACK / REDUCE SIZE"
            allows = False
            risk_multiplier = 0.55
            confidence = min(96.0, 62 + vol_percentile * 0.25 + efficiency * 20)
        elif strong_trend:
            code = "TRENDING"
            label = f"{'🟢' if direction == 'LONG' else '🔴'} Trending {direction.title()}"
            execution_mode = "TREND FOLLOWING"
            allows = volatility_state != "EXTREME"
            risk_multiplier = 1.0 if volatility_state == "NORMAL" else 0.8
            confidence = min(
                96.0,
                45 + trend_strength * 0.35 + efficiency * 35 + direction_consistency * 12,
            )
        elif compressed:
            code = "COMPRESSION"
            label = "🟣 Volatility Compression"
            execution_mode = "WAIT FOR CONFIRMED BREAKOUT"
            allows = False
            risk_multiplier = 0.5
            confidence = min(92.0, 58 + (20 - vol_percentile) * 0.8 + (0.65 - ema_spread_atr) * 20)
        elif choppy:
            code = "RANGING"
            label = "🟡 Ranging / Choppy"
            execution_mode = "AVOID TREND ENTRIES"
            allows = False
            risk_multiplier = 0.45
            confidence = min(94.0, 58 + (0.27 - efficiency) * 90 + (0.75 - ema_spread_atr) * 15)
        else:
            code = "TRANSITION"
            label = "⚪ Transitional / Mixed"
            execution_mode = "REQUIRE EXTRA CONFIRMATION"
            allows = False
            risk_multiplier = 0.65
            confidence = min(88.0, 52 + abs(trend_strength - 50) * 0.25 + direction_consistency * 10)

        if not reasons:
            reasons.append("Trend and volatility evidence are mixed")

        return RegimeResult(
            code=code,
            label=label,
            confidence=max(0.0, confidence),
            trend_strength=trend_strength,
            efficiency=efficiency * 100.0,
            volatility_percentile=vol_percentile,
            volatility_state=volatility_state,
            direction=direction,
            execution_mode=execution_mode,
            allows_trend_entry=allows,
            risk_multiplier=risk_multiplier,
            reasons=tuple(reasons[:4]),
        ).as_dict()

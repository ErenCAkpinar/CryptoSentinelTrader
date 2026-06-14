"""
Math Engine — Pure mathematical signal scoring and position sizing.
Calculates technical scores based on price action and indicators.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger("sentinel.ai_engine.math")

class MathEngine:
    """Mathematical decision engine for scoring market snapshots."""

    def analyze(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze a market snapshot and return technical scores."""
        ind = snapshot.get("indicators", {})
        price = snapshot.get("price", {})
        vol = snapshot.get("volume", {})
        
        # 1. Trend Score (0-100)
        trend_score = 50
        trend = price.get("trend", "SIDEWAYS")
        strength = price.get("trend_strength", 0.5)
        
        if trend == "UP":
            trend_score = 50 + (strength * 50)
        elif trend == "DOWN":
            trend_score = 50 - (strength * 50)
            
        # 2. Momentum Score (RSI based)
        rsi = ind.get("rsi_14", 50)
        momentum_score = 50
        if rsi < 30:
            momentum_score = 80 # Oversold bounce potential
        elif rsi > 70:
            momentum_score = 20 # Overbought correction risk
        elif 30 <= rsi < 45:
            momentum_score = 65
        elif 55 < rsi <= 70:
            momentum_score = 35

        # 3. Volume Score
        vol_ratio = vol.get("volume_ratio", 1.0)
        buy_sell = vol.get("buy_sell_ratio", 0.5)
        volume_score = 50
        if vol_ratio > 1.5:
            if buy_sell > 0.6: volume_score = 75
            elif buy_sell < 0.4: volume_score = 25
            
        # 4. Bollinger Band Score
        bb_pos = ind.get("bb_position_pct", 50)
        bb_score = 100 - bb_pos # Lower price relative to BB = higher score for Long

        # 5. Technical Score (Composite of indicators)
        ema_align = ind.get("ema_alignment", "MIXED")
        tech_base = 50
        if ema_align == "BULLISH": tech_base += 20
        elif ema_align == "BEARISH": tech_base -= 20
        
        technical_score = (tech_base * 0.4) + (momentum_score * 0.3) + (bb_score * 0.3)

        # 6. Composite Score (Final)
        composite_score = (trend_score * 0.4) + (technical_score * 0.4) + (volume_score * 0.2)
        
        # 7. Kelly Fraction (Simplified)
        # We want to risk more when composite_score is high (> 60) or low (< 40)
        edge = abs(composite_score - 50) / 100
        # Simple Kelly: edge / odds. Assuming 1:1 reward risk here for simplicity.
        kelly_fraction = max(0, min(edge, 0.25)) # Cap at 25%

        # 8. Signal
        signal = "NEUTRAL"
        if composite_score > 65: signal = "STRONG_LONG"
        elif composite_score > 56: signal = "WEAK_LONG"
        elif composite_score < 35: signal = "STRONG_SHORT"
        elif composite_score < 44: signal = "WEAK_SHORT"

        return {
            "technical_score": round(technical_score, 1),
            "trend_score": round(trend_score, 1),
            "momentum_score": round(momentum_score, 1),
            "volume_score": round(volume_score, 1),
            "bb_score": round(bb_score, 1),
            "composite_score": round(composite_score, 1),
            "kelly_fraction": round(kelly_fraction, 3),
            "optimal_position_size_usd": 0.0, # Placeholder, set by executor
            "signal": signal
        }

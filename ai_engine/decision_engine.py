"""
AI Decision Engine — Hybrid math + LLM analysis
Receives market_snapshot.json → outputs decision with confidence + risk params
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("sentinel.ai_engine")


class MathEngine:
    """Pure mathematical analysis — runs on EVERY snapshot, zero cost, <10ms"""

    def analyze(self, snapshot: dict) -> dict:
        """Score the market state using technical indicators"""
        indicators = snapshot.get("indicators", {})
        price = snapshot.get("price", {})
        volume = snapshot.get("volume", {})

        # Trend score (0-100)
        trend_score = 50
        if price.get("trend") == "UP":
            trend_score = 50 + min(price.get("trend_strength", 0) * 50, 40)
        elif price.get("trend") == "DOWN":
            trend_score = 50 - min(price.get("trend_strength", 0) * 50, 40)

        # EMA alignment bonus
        if indicators.get("ema_alignment") == "BULLISH":
            trend_score = min(trend_score + 10, 100)
        elif indicators.get("ema_alignment") == "BEARISH":
            trend_score = max(trend_score - 10, 0)

        # Momentum score (RSI-based)
        rsi = indicators.get("rsi_14", 50)
        if 40 <= rsi <= 60:
            momentum_score = 50  # Neutral
        elif 30 <= rsi < 40:
            momentum_score = 65  # Oversold = buying opportunity
        elif rsi < 30:
            momentum_score = 80  # Strongly oversold
        elif 60 < rsi <= 70:
            momentum_score = 40  # Approaching overbought
        else:
            momentum_score = 20  # Overbought = danger

        # Volume score
        vol_ratio = volume.get("volume_ratio", 1.0)
        buy_sell = volume.get("buy_sell_ratio", 0.5)
        volume_score = 50
        if vol_ratio > 1.5 and buy_sell > 0.6:
            volume_score = 75  # High volume + buyers dominant
        elif vol_ratio > 1.5 and buy_sell < 0.4:
            volume_score = 25  # High volume + sellers dominant
        elif vol_ratio < 0.5:
            volume_score = 40  # Low volume = weak moves

        # Volatility score (ATR-based)
        atr = indicators.get("atr_14", 0)
        current = price.get("current", 1)
        atr_pct = (atr / current * 100) if current > 0 else 0
        volatility_score = max(0, min(100, 100 - atr_pct * 20))

        # Composite
        composite = (
            trend_score * 0.35
            + momentum_score * 0.25
            + volume_score * 0.25
            + volatility_score * 0.15
        )

        # Kelly criterion (simplified)
        win_rate = composite / 100  # Use composite as proxy for win probability
        avg_win = 0.02  # 2% average win (configurable)
        avg_loss = 0.01  # 1% average loss (configurable)
        kelly = 0.0
        if avg_loss > 0:
            kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
            kelly = max(0, min(kelly, 0.25))  # Cap at 25%

        # Signal determination
        if composite > 70:
            signal = "STRONG_LONG"
        elif composite > 55:
            signal = "WEAK_LONG"
        elif composite < 30:
            signal = "STRONG_SHORT"
        elif composite < 45:
            signal = "WEAK_SHORT"
        else:
            signal = "NEUTRAL"

        return {
            "technical_score": round(composite, 1),
            "trend_score": round(trend_score, 1),
            "momentum_score": round(momentum_score, 1),
            "volume_score": round(volume_score, 1),
            "volatility_score": round(volatility_score, 1),
            "composite_score": round(composite, 1),
            "kelly_fraction": round(kelly, 4),
            "optimal_position_size_usd": 0,  # Calculated in risk layer
            "signal": signal,
        }


class LLMEngine:
    """LLM-based analysis — called sparingly (anomalies, periodic review)"""

    def __init__(self, config: dict):
        self.config = config
        self.last_call_time = None
        self.call_interval = config.get("ai_engine", {}).get("llm_call_interval_sec", 60)
        self.daily_cost = 0.0
        self.max_daily_cost = config.get("ai_engine", {}).get("max_daily_llm_cost_usd", 5.0)

    def should_call(self, snapshot: dict) -> bool:
        """Determine if LLM should be called based on conditions"""
        anomaly = snapshot.get("anomaly_detection", {})

        # Always call on anomaly
        if anomaly.get("is_anomaly", False):
            return True

        # Periodic call based on interval
        if self.last_call_time is None:
            return True

        elapsed = (datetime.utcnow() - self.last_call_time).total_seconds()
        if elapsed >= self.call_interval:
            return True

        # Cost limit
        if self.daily_cost >= self.max_daily_cost:
            return False

        return False

    async def analyze(self, snapshot: dict) -> Optional[dict]:
        """Call LLM with market snapshot for contextual analysis"""
        if not self.should_call(snapshot):
            return None

        self.last_call_time = datetime.utcnow()

        # Build prompt from snapshot
        prompt = self._build_prompt(snapshot)

        try:
            # TODO: Replace with actual Gemini/Claude API call
            # For now, return a placeholder
            logger.info("🤖 LLM called (model: %s)", self.config.get("ai_engine", {}).get("primary_llm", "gemini"))

            # Estimated cost tracking
            self.daily_cost += 0.003  # ~$0.003 per Gemini Flash call

            return {
                "model_used": self.config.get("ai_engine", {}).get("primary_llm", "gemini-2.5-flash"),
                "analysis": "LLM analysis placeholder — integrate Gemini/Claude API",
                "sentiment": "NEUTRAL",
                "confidence": 0.5,
                "key_factors": [],
                "risk_flags": [],
                "tokens_used": 0,
                "latency_ms": 0,
            }

        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return None

    def _build_prompt(self, snapshot: dict) -> str:
        """Build analysis prompt from market snapshot"""
        price = snapshot.get("price", {})
        volume = snapshot.get("volume", {})
        indicators = snapshot.get("indicators", {})
        anomaly = snapshot.get("anomaly_detection", {})

        return f"""Analyze this crypto market snapshot and provide a trading recommendation.

MARKET DATA:
- Symbol: {snapshot.get('symbol')}
- Price: ${price.get('current', 0):.2f}
- 5m change: {price.get('change_5m_pct', 0):.3f}%
- Trend: {price.get('trend')} (strength: {price.get('trend_strength', 0):.2f})

VOLUME:
- Volume ratio vs 24h avg: {volume.get('volume_ratio', 1):.2f}x
- Buy/Sell ratio: {volume.get('buy_sell_ratio', 0.5):.2f}
- Volume spike: {volume.get('is_spike', False)}

INDICATORS:
- RSI(14): {indicators.get('rsi_14', 50):.1f}
- EMA alignment: {indicators.get('ema_alignment', 'UNKNOWN')}
- MACD histogram: {indicators.get('macd_histogram', 0):.2f}

ANOMALY:
- Detected: {anomaly.get('is_anomaly', False)}
- Score: {anomaly.get('anomaly_score', 0):.2f}

Respond in JSON format with: sentiment, confidence (0-1), key_factors (list), risk_flags (list), recommended_action (HOLD/ENTER_LONG/ENTER_SHORT/EXIT).
"""


class DecisionEngine:
    """Combines math engine + LLM engine with consensus voting"""

    def __init__(self, config: dict):
        self.config = config
        self.math_engine = MathEngine()
        self.llm_engine = LLMEngine(config)
        self.math_weight = config.get("ai_engine", {}).get("math_weight", 0.6)
        self.llm_weight = config.get("ai_engine", {}).get("llm_weight", 0.4)

    async def analyze(self, snapshot: dict) -> dict:
        """Full analysis pipeline: math + optional LLM → consensus → risk params"""
        snapshot_id = snapshot.get("meta", {}).get("snapshot_id", "unknown")

        # Step 1: Math engine (always runs)
        math_result = self.math_engine.analyze(snapshot)

        # Step 2: LLM engine (conditional)
        llm_result = await self.llm_engine.analyze(snapshot)

        # Step 3: Consensus
        if llm_result:
            # Weighted consensus between math and LLM
            math_conf = math_result["composite_score"] / 100
            llm_conf = llm_result.get("confidence", 0.5)
            weighted_conf = math_conf * self.math_weight + llm_conf * self.llm_weight
            agreement = "FULL" if abs(math_conf - llm_conf) < 0.15 else "PARTIAL"
        else:
            # Math-only decision
            weighted_conf = math_result["composite_score"] / 100
            agreement = "MATH_ONLY"

        # Step 4: Determine action
        action = self._determine_action(math_result, llm_result, weighted_conf, snapshot)

        # Step 5: Calculate risk parameters based on confidence
        risk_params = self._calculate_risk(weighted_conf, snapshot)

        return {
            "snapshot_id": snapshot_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "decision": {
                "action": action,
                "confidence": round(weighted_conf, 3),
                "urgency": "HIGH" if snapshot.get("anomaly_detection", {}).get("is_anomaly") else "LOW",
                "reasoning_short": f"Math: {math_result['signal']} ({math_result['composite_score']:.0f}/100)",
            },
            "math_engine_output": math_result,
            "llm_engine_output": llm_result,
            "consensus": {
                "math_weight": self.math_weight,
                "llm_weight": self.llm_weight,
                "weighted_confidence": round(weighted_conf, 3),
                "agreement": agreement,
                "final_action": action,
            },
            "risk_parameters": risk_params,
        }

    def _determine_action(
        self,
        math_result: dict,
        llm_result: Optional[dict],
        confidence: float,
        snapshot: dict,
    ) -> str:
        """Determine final trading action"""
        signal = math_result["signal"]
        has_position = snapshot.get("open_position", {}).get("has_position", False)

        # Emergency stop on extreme anomaly
        anomaly_score = snapshot.get("anomaly_detection", {}).get("anomaly_score", 0)
        if anomaly_score > 0.9:
            return "STOP"

        # Position management
        if has_position:
            position_side = snapshot.get("open_position", {}).get("side", "")
            pnl = snapshot.get("open_position", {}).get("current_pnl_pct", 0)

            # Exit on signal reversal
            if position_side == "LONG" and signal in ("STRONG_SHORT", "WEAK_SHORT"):
                return "EXIT"
            if position_side == "SHORT" and signal in ("STRONG_LONG", "WEAK_LONG"):
                return "EXIT"

            return "HOLD"

        # New position entry
        if confidence > 0.6 and signal == "STRONG_LONG":
            return "ENTER_LONG"
        if confidence > 0.6 and signal == "STRONG_SHORT":
            return "ENTER_SHORT"

        return "HOLD"

    def _calculate_risk(self, confidence: float, snapshot: dict) -> dict:
        """Adaptive risk based on confidence tier"""
        risk_config = self.config.get("risk", {})

        if confidence < 0.4:
            tier = "LOW"
            risk_pct = risk_config.get("low_confidence_risk", 0.01)
            max_leverage = 2
        elif confidence < 0.7:
            tier = "MEDIUM"
            risk_pct = risk_config.get("default_risk_per_trade", 0.03)
            max_leverage = 5
        else:
            tier = "HIGH"
            risk_pct = risk_config.get("high_confidence_risk", 0.05)
            max_leverage = min(8, risk_config.get("max_leverage", 8))

        # ATR-based stop loss
        atr = snapshot.get("indicators", {}).get("atr_14", 0)
        current = snapshot.get("price", {}).get("current", 1)
        sl_distance = max(atr * 1.5 / current * 100, 0.3)  # Min 0.3%
        tp_distance = sl_distance * 3  # 3:1 RR ratio

        return {
            "confidence_tier": tier,
            "risk_per_trade_pct": risk_pct * 100,
            "max_leverage": max_leverage,
            "stop_loss_distance_pct": round(sl_distance, 2),
            "take_profit_distance_pct": round(tp_distance, 2),
            "risk_reward_ratio": 3.0,
        }

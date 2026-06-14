"""
AI Decision Engine — Multi-LLM Ensemble (Stable Version)
Uses Gemini 1.5 Flash and Pro via the stable google-generativeai library.
"""

import math
import json
import logging
import asyncio
from collections import deque
from datetime import datetime
from typing import Optional, List, Dict, Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import anthropic
import numpy as np
from sentinel.ai_engine.decision_engine import MathEngine

logger = logging.getLogger("sentinel.ai_engine.ensemble")

class LLMResponse(BaseModel):
    sentiment: str = Field(..., pattern="^(BULLISH|BEARISH|NEUTRAL)$")
    confidence: float = Field(..., ge=0, le=1)
    recommended_action: str = Field(..., pattern="^(ENTER_LONG|ENTER_SHORT|EXIT|HOLD)$")
    key_factors: List[str] = []
    risk_flags: List[str] = []

class MarketAnalytics:
    def __init__(self, max_len: int = 200):
        self._prices: Dict[str, deque] = {}
        self._max_len = max_len

    def update(self, symbol: str, price: float):
        if symbol not in self._prices:
            self._prices[symbol] = deque(maxlen=self._max_len)
        self._prices[symbol].append(price)

    def get_hurst(self, symbol: str) -> float:
        prices = self._prices.get(symbol)
        if not prices or len(prices) < 30: return 0.5
        series = list(prices)
        returns = [math.log(series[i] / series[i-1]) for i in range(1, len(series)) if series[i-1] > 0]
        if len(returns) < 20: return 0.5
        lags, rs_values = [], []
        for lag in [10, 20, 30, 50, 80]:
            if lag >= len(returns): continue
            chunks = [returns[i:i+lag] for i in range(0, len(returns)-lag+1, lag)]
            rs_list = []
            for chunk in chunks:
                if len(chunk) < lag: continue
                mean_r = sum(chunk) / len(chunk)
                cumsum = []
                s = 0
                for r in chunk:
                    s += r - mean_r
                    cumsum.append(s)
                R = max(cumsum) - min(cumsum)
                S = (sum((r - mean_r) ** 2 for r in chunk) / len(chunk)) ** 0.5
                if S > 1e-12: rs_list.append(R / S)
            if rs_list:
                lags.append(math.log(lag))
                rs_values.append(math.log(sum(rs_list)/len(rs_list)))
        if len(lags) < 2: return 0.5
        n, sx, sy = len(lags), sum(lags), sum(rs_values)
        sxy, sxx = sum(x*y for x,y in zip(lags, rs_values)), sum(x*x for x in lags)
        denom = n * sxx - sx ** 2
        return max(0.0, min(1.0, (n*sxy - sx*sy)/denom)) if abs(denom) > 1e-12 else 0.5

class GeminiEngine:
    def __init__(self, config: Dict, model_id: str):
        api_key = config.get("ai_engine", {}).get("gemini_api_key")
        if api_key:
            self.client = genai.Client(api_key=api_key)
            self.model_id = model_id
        else: 
            self.client = None

    async def analyze(self, snapshot: Dict) -> Optional[LLMResponse]:
        if not self.client: return None
        try:
            p = snapshot.get("price", {})
            prompt = f"""
            Analyze {snapshot.get('symbol')} at ${p.get('current')}.
            Indicators: {json.dumps(snapshot.get('indicators', {}))}
            
            Respond with a valid JSON object matching this schema:
            - sentiment: "BULLISH", "BEARISH", or "NEUTRAL"
            - confidence: float between 0.0 and 1.0
            - recommended_action: "ENTER_LONG", "ENTER_SHORT", "EXIT", or "HOLD"
            - key_factors: list of strings
            - risk_flags: list of strings
            """
            
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=LLMResponse
                )
            )
            return LLMResponse.model_validate_json(response.text)
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            return None

class ClaudeEngine:
    def __init__(self, config: Dict):
        api_key = config.get("ai_engine", {}).get("anthropic_api_key")
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None

    async def analyze(self, snapshot: Dict) -> Optional[LLMResponse]:
        if not self.client: return None
        try:
            prompt = f"Analyze {snapshot.get('symbol')}. Respond ONLY with JSON matching the LLMResponse schema."
            response = await asyncio.to_thread(
                self.client.messages.create,
                model="claude-3-5-sonnet-20241022",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}]
            )
            return LLMResponse.model_validate_json(response.content[0].text)
        except Exception as e:
            logger.error(f"Claude error: {e}")
            return None

class EnsembleLLM:
    def __init__(self, config: Dict):
        self.flash = GeminiEngine(config, "gemini-2.5-flash")
        self.pro = GeminiEngine(config, "gemini-2.5-pro")
        self.claude = ClaudeEngine(config)

    async def analyze(self, snapshot: Dict) -> Dict[str, Any]:
        tasks = [self.flash.analyze(snapshot), self.pro.analyze(snapshot), self.claude.analyze(snapshot)]
        results = await asyncio.gather(*tasks)
        
        # Filter out None results (failed API calls)
        valid = []
        for name, res in zip(["flash", "pro", "claude"], results):
            if res:
                valid.append((name, res))
        
        if not valid:
            # If ALL models fail, provide a neutral fallback based on MathEngine (Layer 3 fallback)
            logger.warning("All LLMs failed or hit quota. Using neutral fallback.")
            return {"weighted_confidence": 0.5, "models": [], "fallback": True}
            
        weights = {"flash": 0.35, "pro": 0.25, "claude": 0.40}
        
        # Re-calculate weights based on which models actually responded
        total_w = sum(weights[n] for n, _ in valid)
        conf = sum(r.confidence * (weights[n]/total_w) for n, r in valid)
        
        return {
            "weighted_confidence": round(conf, 3), 
            "models": [n for n, _ in valid],
            "consensus_count": len(valid)
        }

class DecisionEngine:
    def __init__(self, config: Dict):
        self.math_engine = MathEngine()
        self.ensemble = EnsembleLLM(config)
        self.analytics = MarketAnalytics()

    async def analyze(self, snapshot: Dict) -> Dict:
        symbol = snapshot.get("symbol", "unknown")
        price = snapshot.get("price", {}).get("current", 0)
        self.analytics.update(symbol, price)
        math_res = self.math_engine.analyze(snapshot)
        llm_res = await self.ensemble.analyze(snapshot)
        math_conf = math_res["composite_score"] / 100
        llm_conf = llm_res.get("weighted_confidence", 0.5)
        final_conf = (math_conf * 0.6) + (llm_conf * 0.4)
        
        action = "HOLD"
        # Lowered thresholds for more frequent trading (opportunistic mode)
        if final_conf >= 0.55: action = "ENTER_LONG"
        elif final_conf <= 0.45: action = "ENTER_SHORT"

        # Determine confidence tier for risk management
        if final_conf >= 0.75 or final_conf <= 0.25: tier = "HIGH"
        elif final_conf >= 0.65 or final_conf <= 0.35: tier = "MEDIUM"
        else: tier = "LOW"

        return {
            "decision": {
                "action": action, 
                "confidence": round(final_conf, 3),
                "confidence_tier": tier
            }, 
            "math_engine_output": math_res, 
            "llm_ensemble": llm_res,
            "consensus": {
                "weighted_confidence": round(final_conf, 3)
            },
            "risk_parameters": {
                "kurtosis_atr_mult": 1.0,
                "confidence_tier": tier
            }
        }

"""
Whale Scorer — Assigns scores to Polymarket traders based on multi-dimensional performance.
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger("sentinel.wallet_intel.scorer")

class WhaleScorer:
    def __init__(self, config: Dict):
        self.config = config
        self.weights = {
            "profitability": 0.30,
            "timing_skill": 0.20,
            "low_slippage": 0.15,
            "consistency": 0.15,
            "market_selection": 0.10,
            "recency": 0.10
        }

    def score_whale(self, whale_data: Dict[str, Any], trade_history: List[Dict[str, Any]]) -> float:
        """Assign a combined score between 0.0 and 1.0."""
        # Note: In a full implementation, we'd analyze 'trade_history' to compute metrics
        # For now, we'll implement a skeleton based on the weights.
        
        # 1. Profitability (normalized)
        profit_score = self._normalize(whale_data.get("profit", 0), 0, 1_000_000)
        
        # 2. Timing skill (win rate over time)
        timing_score = 0.5 # Default
        if trade_history:
            wins = sum(1 for t in trade_history if t.get("pnl", 0) > 0)
            timing_score = wins / len(trade_history)

        # 3. Consistency (standard deviation of daily returns)
        # Mocking for now
        consistency_score = 0.7

        # 4. Recency (active in last 48 hours?)
        recency_score = 0.8 # Mocking for now

        # Calculate final weighted score
        final_score = (
            profit_score * self.weights["profitability"] +
            timing_score * self.weights["timing_skill"] +
            consistency_score * self.weights["consistency"] +
            recency_score * self.weights["recency"] +
            0.5 * (self.weights["low_slippage"] + self.weights["market_selection"]) # Defaults
        )

        return round(final_score, 3)

    def _normalize(self, value: float, min_val: float, max_val: float) -> float:
        """Helper to clamp and normalize values to [0, 1]."""
        if max_val == min_val: return 0.5
        normalized = (value - min_val) / (max_val - min_val)
        return max(0.0, min(1.0, normalized))

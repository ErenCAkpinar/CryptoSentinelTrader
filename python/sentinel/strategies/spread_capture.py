"""
Spread Capture (Market Making) Strategy.
Places limit orders on both sides of the spread to capture the market-making premium.
Uses Dynamic Spread Scaling based on ATR and HMM Volatility.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger("sentinel.strategies.spread_capture")

class SpreadCaptureStrategy:
    def __init__(self, config: Dict):
        self.config = config
        self.base_spread_bps = 5 # 0.05%
        self.inventory_limit = 0.1 # Max 10% skew in inventory

    def calculate_orders(self, current_price: float, atr: float, regime: str) -> Dict[str, Any]:
        """Calculate optimal Bid and Ask levels."""
        
        # 1. Scale spread based on market regime
        multiplier = 1.0
        if regime == "HIGH_VOLATILITY": multiplier = 2.5
        elif regime == "TRENDING": multiplier = 1.5
        
        # 2. Dynamic spread based on ATR (standard deviation of price)
        dynamic_spread = max(self.base_spread_bps / 10000, (atr * 0.5) / current_price)
        final_spread = dynamic_spread * multiplier

        bid_price = current_price * (1 - final_spread)
        ask_price = current_price * (1 + final_spread)

        return {
            "bid": round(bid_price, 2),
            "ask": round(ask_price, 2),
            "spread_pct": round(final_spread * 2 * 100, 3),
            "regime": regime
        }

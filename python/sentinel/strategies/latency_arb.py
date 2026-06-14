"""
Latency Arbitrage Strategy — Binance vs Polymarket.
Exploits the execution lag of binary outcome markets on Polymarket
relative to high-frequency spot moves on Binance.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("sentinel.strategies.latency_arb")

class LatencyArbStrategy:
    def __init__(self, config: Dict):
        self.config = config
        self.min_discrepancy_pct = config.get("strategies", {}).get("min_arb_discrepancy", 0.3) / 100
        self.fee_allowance = 0.001 # 0.1% allowance for entry/exit fees

    def analyze(self, binance_price: float, polymarket_price: float) -> Optional[Dict[str, Any]]:
        """
        Check for price lag. 
        Example: Binance BTC price is $100k, Polymarket 'BTC Above $99k' is priced at 0.95 ($95k).
        Discrepancy = 5%.
        """
        if binance_price <= 0 or polymarket_price <= 0: return None

        # Normalized comparison (implied probability vs spot)
        diff = (binance_price - polymarket_price) / binance_price
        abs_diff = abs(diff)

        if abs_diff > (self.min_discrepancy_pct + self.fee_allowance):
            side = "ENTER_LONG" if diff > 0 else "ENTER_SHORT"
            confidence = min(abs_diff * 10, 1.0) # Scaled confidence
            
            logger.info(f"⚡ ARB OPPORTUNITY: {side} | Diff: {abs_diff*100:.2f}% | Conf: {confidence:.2f}")
            
            return {
                "action": side,
                "confidence": confidence,
                "strategy": "LATENCY_ARB",
                "reason": f"Discrepancy {abs_diff*100:.2f}% > Threshold"
            }

        return None

"""
Whale Cluster Detector — Identifies convergence between top traders.
Logic: If 3+ whales from our watchlist enter the same market within a 2-hour window, 
generate a CONVERGENCE_ALERT to boost trade confidence.
"""

import logging
from typing import List, Dict, Any, Set
from datetime import datetime, timedelta

logger = logging.getLogger("sentinel.wallet_intel.cluster")

class ConvergenceDetector:
    def __init__(self, config: Dict):
        self.config = config
        self.threshold = config.get("wallet_intel", {}).get("whale_threshold", 3)
        self.window_hours = config.get("wallet_intel", {}).get("window_hours", 2)
        # symbol -> list of (whale_address, timestamp, side)
        self.active_whale_trades: Dict[str, List[Dict[str, Any]]] = {}

    def record_whale_trade(self, symbol: str, whale_address: str, side: str):
        """Record a new trade from a tracked whale."""
        if symbol not in self.active_whale_trades:
            self.active_whale_trades[symbol] = []
        
        self.active_whale_trades[symbol].append({
            "address": whale_address,
            "timestamp": datetime.utcnow(),
            "side": side
        })
        
        # Clean up stale trades outside the window
        self._cleanup(symbol)
        
        # Check for convergence
        return self.check_convergence(symbol)

    def check_convergence(self, symbol: str) -> Dict[str, Any]:
        """Check if enough whales have converged on the same side."""
        trades = self.active_whale_trades.get(symbol, [])
        if not trades: return {"converged": False}

        long_whales = {t["address"] for t in trades if t["side"] == "LONG"}
        short_whales = {t["address"] for t in trades if t["side"] == "SHORT"}

        if len(long_whales) >= self.threshold:
            logger.info(f"🔥 WHALE CONVERGENCE [LONG] on {symbol} | {len(long_whales)} whales!")
            return {
                "converged": True,
                "side": "LONG",
                "count": len(long_whales),
                "whales": list(long_whales)
            }
        
        if len(short_whales) >= self.threshold:
            logger.info(f"🔥 WHALE CONVERGENCE [SHORT] on {symbol} | {len(short_whales)} whales!")
            return {
                "converged": True,
                "side": "SHORT",
                "count": len(short_whales),
                "whales": list(short_whales)
            }

        return {"converged": False}

    def _cleanup(self, symbol: str):
        """Remove trades older than the window."""
        cutoff = datetime.utcnow() - timedelta(hours=self.window_hours)
        if symbol in self.active_whale_trades:
            self.active_whale_trades[symbol] = [
                t for t in self.active_whale_trades[symbol] 
                if t["timestamp"] > cutoff
            ]

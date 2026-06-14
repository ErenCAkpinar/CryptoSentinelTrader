"""
Circuit Breaker Module — Protects capital by halting trading during extreme conditions.
- Daily Loss Limit: Stops trading if total loss for the day exceeds threshold.
- Regime Protection: Pauses trading during high-volatility/crash regimes.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("sentinel.risk.circuit_breaker")

class CircuitBreaker:
    def __init__(self, config: dict):
        self.config = config
        self.daily_loss_limit = config.get("risk", {}).get("daily_loss_limit_pct", 0.05)
        self.is_tripped = False
        self.trip_reason = None
        self.trip_time = None

    def evaluate(self, balance: float, daily_pnl: float, regime: str = "NORMAL"):
        """Check for circuit breaker conditions."""
        
        # 1. Daily Loss Check
        if balance > 0 and daily_pnl < 0:
            loss_pct = abs(daily_pnl) / balance
            if loss_pct >= self.daily_loss_limit:
                self.trip("Daily Loss Limit Reached", f"{loss_pct*100:.1f}% loss")
                return True

        # 2. Crash Regime Check
        if regime == "CRASH":
            self.trip("Market Crash Regime", "Halting for safety")
            return True

        return False

    def trip(self, reason: str, detail: str):
        """Trip the circuit breaker."""
        if not self.is_tripped:
            self.is_tripped = True
            self.trip_reason = reason
            self.trip_time = datetime.now(timezone.utc)
            logger.critical(f"🛑 CIRCUIT BREAKER TRIPPED: {reason} | {detail}")

    def reset(self):
        """Reset the circuit breaker (usually at UTC midnight)."""
        self.is_tripped = False
        self.trip_reason = None
        self.trip_time = None
        logger.info("⚡ Circuit breaker reset.")

    def can_trade(self) -> bool:
        """Return true if it's safe to open new trades."""
        return not self.is_tripped

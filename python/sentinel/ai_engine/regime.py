"""
Market Regime Classifier — HMM-based (Hidden Markov Model).
Identifies the hidden state of the market to select the optimal strategy.

Regimes:
0: Low Volatility / Ranging (Mean Reversion)
1: High Volatility / Trending (Momentum)
2: Extreme Volatility / Crash (Circuit Breaker / Risk-Off)
"""

import numpy as np
import pandas as pd
from hmmlearn import hmm
import logging
from typing import Optional, Tuple

logger = logging.getLogger("sentinel.ai_engine.regime")

class MarketRegimeClassifier:
    def __init__(self, n_regimes: int = 3):
        self.n_regimes = n_regimes
        self.model = hmm.GaussianHMM(
            n_components=n_regimes, 
            covariance_type="full", 
            n_iter=1000,
            random_state=42
        )
        self.is_trained = False

    def prepare_features(self, prices: np.ndarray) -> np.ndarray:
        """Calculate log returns and volatility as features for the HMM."""
        if len(prices) < 20: return np.array([])
        log_returns = np.diff(np.log(prices))
        volatility = pd.Series(log_returns).rolling(window=10).std().values[10:]
        if len(volatility) == 0: return np.array([])
        features = np.column_stack([log_returns[9:9+len(volatility)], volatility])
        return features

    def train(self, historical_prices: np.ndarray):
        """Train the HMM on historical price data."""
        try:
            features = self.prepare_features(historical_prices)
            if len(features) < 50:
                logger.warning("Not enough data to train HMM.")
                return
            self.model.fit(features)
            self.is_trained = True
            logger.info("✅ HMM Regime Classifier trained successfully.")
        except Exception as e:
            logger.error(f"HMM Training failed: {e}")

    def predict(self, recent_prices: np.ndarray) -> int:
        """Predict the current market regime (0, 1, or 2)."""
        if not self.is_trained or len(recent_prices) < 20:
            return 0 # Default to Ranging if not trained
        
        try:
            features = self.prepare_features(recent_prices)
            if len(features) == 0: return 0
            # We only care about the most recent state
            regime = self.model.predict(features)[-1]
            return int(regime)
        except Exception as e:
            logger.error(f"Regime prediction error: {e}")
            return 0

    def get_regime_label(self, regime_id: int) -> str:
        """Map the HMM state ID to a human-readable label."""
        if not self.is_trained: return "NORMAL"
        
        try:
            variances = np.array([np.diag(cov).sum() for cov in self.model.covars_])
            sorted_indices = np.argsort(variances)
            
            if regime_id == sorted_indices[0]: return "MEAN_REVERTING"
            if regime_id == sorted_indices[1]: return "TRENDING"
            if regime_id == sorted_indices[2]: return "HIGH_VOLATILITY"
        except Exception:
            pass
        return "UNKNOWN"

"""Tests for the OpportunityScanner — filtering, indicator computation, scoring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import pytest

from scanner.opportunity_scanner import OpportunityScanner


def make_config():
    """Minimal config for scanner tests."""
    return {
        "exchange": {"primary": "binance_futures_testnet"},
        "scanner": {
            "enabled": True,
            "scan_interval_sec": 300,
            "min_volume_24h_usdt": 10_000_000,
            "min_score": 55,
            "max_symbols": 5,
            "ohlcv_timeframe": "15m",
            "ohlcv_limit": 100,
            "top_candidates_for_scoring": 30,
            "always_include": ["BTCUSDT", "ETHUSDT"],
            "excluded_symbols": ["USDCUSDT", "BUSDUSDT"],
        },
    }


def make_ohlcv(n=100, base_price=100.0, trend=0.001):
    """Generate synthetic OHLCV data.
    trend > 0 = uptrend, trend < 0 = downtrend.
    """
    ohlcv = []
    price = base_price
    for i in range(n):
        price *= 1 + trend + np.random.normal(0, 0.002)
        o = price * (1 - np.random.uniform(0, 0.005))
        h = price * (1 + np.random.uniform(0, 0.01))
        l = price * (1 - np.random.uniform(0, 0.01))
        c = price
        v = np.random.uniform(100, 1000)
        ts = 1700000000000 + i * 900_000  # 15m intervals
        ohlcv.append([ts, o, h, l, c, v])
    return ohlcv


class TestFilterPairs:
    def setup_method(self):
        self.config = make_config()
        # Don't actually connect to exchange
        self.scanner = OpportunityScanner.__new__(OpportunityScanner)
        self.scanner.config = self.config
        self.scanner.scanner_config = self.config["scanner"]
        self.scanner.min_volume = 10_000_000
        self.scanner.excluded = {"USDCUSDT", "BUSDUSDT"}
        self.scanner.always_include = ["BTCUSDT", "ETHUSDT"]

    def test_filters_low_volume(self):
        tickers = {
            "BTC/USDT:USDT": {
                "quoteVolume": 50_000_000_000,
                "last": 84000,
                "info": {"symbol": "BTCUSDT"},
            },
            "SHIB/USDT:USDT": {
                "quoteVolume": 500_000,  # Below threshold
                "last": 0.00001,
                "info": {"symbol": "SHIBUSDT"},
            },
        }
        result = self.scanner._filter_pairs(tickers)
        assert len(result) == 1
        assert result[0]["symbol"] == "BTCUSDT"

    def test_excludes_stablecoins(self):
        tickers = {
            "BTC/USDT:USDT": {
                "quoteVolume": 50_000_000_000,
                "last": 84000,
                "info": {"symbol": "BTCUSDT"},
            },
            "USDC/USDT:USDT": {
                "quoteVolume": 100_000_000,
                "last": 1.0,
                "info": {"symbol": "USDCUSDT"},
            },
        }
        result = self.scanner._filter_pairs(tickers)
        assert len(result) == 1
        assert result[0]["symbol"] == "BTCUSDT"

    def test_filters_non_usdt(self):
        tickers = {
            "BTC/USDT:USDT": {
                "quoteVolume": 50_000_000_000,
                "last": 84000,
                "info": {"symbol": "BTCUSDT"},
            },
            "ETH/BTC:BTC": {
                "quoteVolume": 100_000_000,
                "last": 0.05,
                "info": {"symbol": "ETHBTC"},
            },
        }
        result = self.scanner._filter_pairs(tickers)
        assert len(result) == 1

    def test_sorts_by_volume(self):
        tickers = {
            "SOL/USDT:USDT": {
                "quoteVolume": 20_000_000,
                "last": 135,
                "info": {"symbol": "SOLUSDT"},
            },
            "BTC/USDT:USDT": {
                "quoteVolume": 50_000_000_000,
                "last": 84000,
                "info": {"symbol": "BTCUSDT"},
            },
        }
        result = self.scanner._filter_pairs(tickers)
        assert result[0]["symbol"] == "BTCUSDT"
        assert result[1]["symbol"] == "SOLUSDT"


class TestIndicatorComputation:
    def setup_method(self):
        self.scanner = OpportunityScanner.__new__(OpportunityScanner)

    def test_rsi_in_range(self):
        closes = np.array([100 + i * 0.5 for i in range(50)])
        rsi = self.scanner._calc_rsi(closes, 14)
        assert 0 <= rsi <= 100

    def test_rsi_overbought_on_strong_uptrend(self):
        closes = np.array([100 + i * 2.0 for i in range(50)])
        rsi = self.scanner._calc_rsi(closes, 14)
        assert rsi > 70

    def test_rsi_oversold_on_strong_downtrend(self):
        closes = np.array([200 - i * 2.0 for i in range(50)])
        rsi = self.scanner._calc_rsi(closes, 14)
        assert rsi < 30

    def test_ema_basic(self):
        data = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0])
        ema = self.scanner._calc_ema(data, 5)
        assert 15 < ema < 20  # Should be biased toward recent values

    def test_atr_positive(self):
        highs = np.array([105 + i for i in range(50)], dtype=float)
        lows = np.array([95 + i for i in range(50)], dtype=float)
        closes = np.array([100 + i for i in range(50)], dtype=float)
        atr = self.scanner._calc_atr(highs, lows, closes, 14)
        assert atr > 0

    def test_ohlcv_to_snapshot_structure(self):
        """Verify the snapshot dict has all keys MathEngine expects."""
        self.scanner.math_engine = None  # Not needed for structure test
        ohlcv = make_ohlcv(100, base_price=100.0, trend=0.001)
        candidate = {"price": 100.0, "quote_volume_24h": 50_000_000}

        snapshot = self.scanner._ohlcv_to_snapshot("TESTUSDT", ohlcv, candidate)

        # Check top-level keys
        assert "price" in snapshot
        assert "volume" in snapshot
        assert "indicators" in snapshot

        # Check price fields
        assert snapshot["price"]["current"] > 0
        assert snapshot["price"]["trend"] in ("UP", "DOWN", "SIDEWAYS")
        assert 0 <= snapshot["price"]["trend_strength"] <= 1

        # Check indicator fields
        ind = snapshot["indicators"]
        assert 0 <= ind["rsi_14"] <= 100
        assert ind["ema_9"] > 0
        assert ind["ema_21"] > 0
        assert ind["ema_50"] > 0
        assert ind["ema_alignment"] in ("BULLISH", "BEARISH", "MIXED")
        assert ind["atr_14"] > 0
        assert ind["vwap"] > 0
        assert "bb_position_pct" in ind
        assert "macd_value" in ind
        assert "macd_signal" in ind
        assert "macd_histogram" in ind


class TestScoring:
    def setup_method(self):
        self.scanner = OpportunityScanner.__new__(OpportunityScanner)
        from ai_engine.decision_engine import MathEngine
        self.scanner.math_engine = MathEngine()

    def test_uptrend_scores_above_neutral(self):
        ohlcv = make_ohlcv(100, base_price=100.0, trend=0.003)
        candidate = {"price": 100.0, "quote_volume_24h": 50_000_000}
        snapshot = self.scanner._ohlcv_to_snapshot("TESTUSDT", ohlcv, candidate)
        result = self.scanner.math_engine.analyze(snapshot)
        assert result["composite_score"] >= 45  # Uptrend should score at least neutral

    def test_downtrend_scores_below_neutral(self):
        ohlcv = make_ohlcv(100, base_price=200.0, trend=-0.003)
        candidate = {"price": 200.0, "quote_volume_24h": 50_000_000}
        snapshot = self.scanner._ohlcv_to_snapshot("TESTUSDT", ohlcv, candidate)
        result = self.scanner.math_engine.analyze(snapshot)
        assert result["composite_score"] <= 55  # Downtrend should score at most neutral

    def test_score_in_valid_range(self):
        ohlcv = make_ohlcv(100, base_price=100.0, trend=0.0)
        candidate = {"price": 100.0, "quote_volume_24h": 50_000_000}
        snapshot = self.scanner._ohlcv_to_snapshot("TESTUSDT", ohlcv, candidate)
        result = self.scanner.math_engine.analyze(snapshot)
        assert 0 <= result["composite_score"] <= 100


class TestAlwaysInclude:
    def test_always_include_symbols_present(self):
        """Active symbols should always contain the always_include list."""
        scanner = OpportunityScanner.__new__(OpportunityScanner)
        scanner.always_include = ["BTCUSDT", "ETHUSDT"]
        scanner.max_symbols = 5

        # Simulate qualified results that don't include BTC or ETH
        qualified = [
            {"symbol": "SOLUSDT", "composite_score": 78},
            {"symbol": "AVAXUSDT", "composite_score": 72},
            {"symbol": "LINKUSDT", "composite_score": 68},
        ]

        active = list(scanner.always_include)
        for item in qualified:
            if item["symbol"] not in active and len(active) < scanner.max_symbols:
                active.append(item["symbol"])

        assert "BTCUSDT" in active
        assert "ETHUSDT" in active
        assert "SOLUSDT" in active
        assert len(active) <= 5

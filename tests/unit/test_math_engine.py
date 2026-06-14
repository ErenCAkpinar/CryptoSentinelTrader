"""Tests for the MathEngine — pure mathematical signal scoring"""

from ai_engine.decision_engine import MathEngine


def make_snapshot(
    price=66000, trend="UP", trend_strength=0.5,
    rsi=55, ema_alignment="BULLISH",
    volume_ratio=1.0, buy_sell_ratio=0.5,
    atr=400, is_anomaly=False,
):
    """Helper to create test snapshots"""
    return {
        "price": {
            "current": price,
            "trend": trend,
            "trend_strength": trend_strength,
        },
        "volume": {
            "volume_ratio": volume_ratio,
            "buy_sell_ratio": buy_sell_ratio,
            "is_spike": volume_ratio > 2.0,
        },
        "indicators": {
            "rsi_14": rsi,
            "ema_alignment": ema_alignment,
            "macd_histogram": 0,
            "atr_14": atr,
        },
        "anomaly_detection": {
            "is_anomaly": is_anomaly,
            "anomaly_score": 0.8 if is_anomaly else 0.1,
        },
    }


class TestMathEngine:
    def setup_method(self):
        self.engine = MathEngine()

    def test_bullish_signal(self):
        """Strong uptrend + bullish EMAs + good RSI → STRONG_LONG"""
        snapshot = make_snapshot(
            trend="UP", trend_strength=0.8,
            rsi=58, ema_alignment="BULLISH",
            volume_ratio=1.5, buy_sell_ratio=0.65,
        )
        result = self.engine.analyze(snapshot)
        assert result["signal"] in ("STRONG_LONG", "WEAK_LONG")
        assert result["composite_score"] > 55

    def test_bearish_signal(self):
        """Strong downtrend + bearish EMAs + high RSI → SHORT"""
        snapshot = make_snapshot(
            trend="DOWN", trend_strength=0.8,
            rsi=75, ema_alignment="BEARISH",
            volume_ratio=1.5, buy_sell_ratio=0.35,
        )
        result = self.engine.analyze(snapshot)
        assert result["signal"] in ("STRONG_SHORT", "WEAK_SHORT")
        assert result["composite_score"] < 45

    def test_neutral_signal(self):
        """Sideways market with low volume → NEUTRAL or near-neutral"""
        snapshot = make_snapshot(
            trend="SIDEWAYS", trend_strength=0.0,
            rsi=50, ema_alignment="MIXED",
            volume_ratio=0.4, buy_sell_ratio=0.5,
        )
        result = self.engine.analyze(snapshot)
        assert result["signal"] in ("NEUTRAL", "WEAK_LONG", "WEAK_SHORT")
        assert 35 <= result["composite_score"] <= 60

    def test_oversold_bounce(self):
        """RSI < 30 = buying opportunity score boost"""
        snapshot = make_snapshot(rsi=25)
        result = self.engine.analyze(snapshot)
        assert result["momentum_score"] == 80

    def test_overbought_warning(self):
        """RSI > 70 = danger zone"""
        snapshot = make_snapshot(rsi=78)
        result = self.engine.analyze(snapshot)
        assert result["momentum_score"] == 20

    def test_volume_spike_with_buyers(self):
        """High volume + buyer dominant = bullish volume score"""
        snapshot = make_snapshot(volume_ratio=2.5, buy_sell_ratio=0.7)
        result = self.engine.analyze(snapshot)
        assert result["volume_score"] == 75

    def test_kelly_fraction_positive(self):
        """Bullish composite → positive Kelly fraction"""
        snapshot = make_snapshot(
            trend="UP", trend_strength=0.9,
            rsi=55, ema_alignment="BULLISH",
            volume_ratio=1.8, buy_sell_ratio=0.65,
        )
        result = self.engine.analyze(snapshot)
        assert result["kelly_fraction"] >= 0

    def test_kelly_fraction_capped(self):
        """Kelly never exceeds 25%"""
        snapshot = make_snapshot(
            trend="UP", trend_strength=1.0,
            rsi=35, ema_alignment="BULLISH",
            volume_ratio=3.0, buy_sell_ratio=0.8,
        )
        result = self.engine.analyze(snapshot)
        assert result["kelly_fraction"] <= 0.25

    def test_all_fields_present(self):
        """Output contains all required fields"""
        snapshot = make_snapshot()
        result = self.engine.analyze(snapshot)
        required = [
            "technical_score", "trend_score", "momentum_score",
            "volume_score", "bb_score", "composite_score",
            "kelly_fraction", "optimal_position_size_usd", "signal",
        ]
        for field in required:
            assert field in result, f"Missing field: {field}"

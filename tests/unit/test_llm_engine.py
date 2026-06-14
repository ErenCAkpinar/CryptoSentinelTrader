"""Tests for LLMEngine — mocks Gemini API to avoid real calls"""

import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _install_genai_stub():
    """Inject a minimal google.generativeai stub so tests run without the real package."""
    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.generativeai")
    genai_mod.configure = MagicMock()
    genai_mod.GenerativeModel = MagicMock(return_value=MagicMock())
    google_mod.generativeai = genai_mod
    sys.modules.setdefault("google", google_mod)
    sys.modules["google.generativeai"] = genai_mod
    return genai_mod


_genai_stub = _install_genai_stub()

from ai_engine.decision_engine import LLMEngine  # noqa: E402


def make_config(api_key="test-key"):
    return {
        "ai_engine": {
            "primary_llm": "gemini-2.5-flash",
            "gemini_api_key": api_key,
            "llm_call_interval_sec": 60,
            "max_daily_llm_cost_usd": 5.0,
        }
    }


def make_snapshot(is_anomaly=False):
    return {
        "symbol": "BTCUSDT",
        "exchange": "binance_futures",
        "price": {
            "current": 66000,
            "trend": "UP",
            "trend_strength": 0.6,
            "change_5m_pct": 0.2,
            "change_15m_pct": 0.5,
            "change_1h_pct": 1.1,
        },
        "volume": {
            "volume_ratio": 1.4,
            "buy_sell_ratio": 0.6,
            "is_spike": False,
        },
        "indicators": {
            "rsi_14": 58,
            "rsi_signal": "NEUTRAL",
            "ema_alignment": "BULLISH",
            "macd_value": 0.002,
            "macd_signal": 0.001,
            "macd_histogram": 0.001,
            "bb_position_pct": 55,
            "ema_9": 65900,
            "ema_21": 65700,
            "ema_50": 65000,
            "atr_14": 350,
            "vwap": 65800,
        },
        "anomaly_detection": {
            "is_anomaly": is_anomaly,
            "anomaly_score": 0.8 if is_anomaly else 0.1,
            "liquidation_cascade_risk": "LOW",
            "detected_patterns": ["VOLUME_SPIKE"] if is_anomaly else [],
        },
        "market_regime": {
            "current": "TRENDING_UP",
            "volatility_regime": "MEDIUM",
        },
    }


def _mock_gemini_response(sentiment="BULLISH", confidence=0.72, action="ENTER_LONG"):
    """Create a mock Gemini response object"""
    payload = json.dumps({
        "sentiment": sentiment,
        "confidence": confidence,
        "recommended_action": action,
        "key_factors": ["RSI neutral", "EMA bullish alignment"],
        "risk_flags": [],
    })
    mock = MagicMock()
    mock.text = payload
    mock.usage_metadata.total_token_count = 120
    return mock


class TestLLMEngineShouldCall:
    def _make_engine(self, **kwargs):
        return LLMEngine(make_config(**kwargs))

    def test_no_api_key_disables_llm(self):
        engine = LLMEngine(make_config(api_key=""))
        assert engine._gemini_model is None
        assert not engine.should_call(make_snapshot())

    def test_first_call_allowed(self):
        engine = self._make_engine()
        assert engine.should_call(make_snapshot())

    def test_anomaly_bypasses_interval(self):
        from datetime import datetime
        engine = self._make_engine()
        engine.last_call_time = datetime.utcnow()  # simulate recent call
        # Normal snapshot → blocked by interval
        assert not engine.should_call(make_snapshot(is_anomaly=False))
        # Anomaly snapshot → allowed immediately
        assert engine.should_call(make_snapshot(is_anomaly=True))

    def test_cost_limit_blocks_calls(self):
        engine = self._make_engine()
        engine.daily_cost = 5.0  # at limit
        assert not engine.should_call(make_snapshot(is_anomaly=True))


class TestLLMEngineAnalyze:
    def _make_engine(self):
        return LLMEngine(make_config())

    def test_returns_none_when_model_missing(self):
        engine = LLMEngine(make_config(api_key=""))
        result = asyncio.get_event_loop().run_until_complete(
            engine.analyze(make_snapshot())
        )
        assert result is None

    def test_successful_gemini_call(self):
        engine = self._make_engine()
        mock_resp = _mock_gemini_response("BULLISH", 0.72, "ENTER_LONG")
        engine._gemini_model.generate_content.return_value = mock_resp

        result = asyncio.get_event_loop().run_until_complete(
            engine.analyze(make_snapshot())
        )

        assert result is not None
        assert result["sentiment"] == "BULLISH"
        assert result["confidence"] == 0.72
        assert result["recommended_action"] == "ENTER_LONG"
        assert "key_factors" in result
        assert "risk_flags" in result
        assert result["latency_ms"] >= 0

    def test_confidence_clamped_to_0_1(self):
        engine = self._make_engine()
        mock_resp = _mock_gemini_response(confidence=1.5)  # out of range
        engine._gemini_model.generate_content.return_value = mock_resp

        result = asyncio.get_event_loop().run_until_complete(
            engine.analyze(make_snapshot())
        )
        assert result["confidence"] <= 1.0

    def test_cost_increments_on_call(self):
        engine = self._make_engine()
        engine._gemini_model.generate_content.return_value = _mock_gemini_response()

        asyncio.get_event_loop().run_until_complete(engine.analyze(make_snapshot()))
        assert engine.daily_cost > 0

    def test_bad_json_returns_none(self):
        engine = self._make_engine()
        mock = MagicMock()
        mock.text = "not valid json {{{"
        engine._gemini_model.generate_content.return_value = mock

        result = asyncio.get_event_loop().run_until_complete(
            engine.analyze(make_snapshot())
        )
        assert result is None

    def test_strips_markdown_fences(self):
        engine = self._make_engine()
        payload = json.dumps({
            "sentiment": "NEUTRAL", "confidence": 0.5,
            "recommended_action": "HOLD",
            "key_factors": [], "risk_flags": [],
        })
        mock = MagicMock()
        mock.text = f"```json\n{payload}\n```"
        mock.usage_metadata.total_token_count = 80
        engine._gemini_model.generate_content.return_value = mock

        result = asyncio.get_event_loop().run_until_complete(
            engine.analyze(make_snapshot())
        )
        assert result is not None
        assert result["sentiment"] == "NEUTRAL"

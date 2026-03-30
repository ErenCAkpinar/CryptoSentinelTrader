"""Load configuration from TOML file with defaults"""

import sys
from pathlib import Path


def load_config(path: str = "config/config.toml") -> dict:
    """Load config from TOML, falling back to defaults"""
    config_path = Path(path)

    if config_path.exists():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        with open(config_path, "rb") as f:
            return tomllib.load(f)

    # Default configuration for development/testing
    return {
        "exchange": {
            "primary": "binance_futures_testnet",
            "api_key": "",
            "api_secret": "",
            "symbols": ["BTCUSDT", "ETHUSDT"],
        },
        "risk": {
            "default_risk_per_trade": 0.03,
            "low_confidence_risk": 0.01,
            "high_confidence_risk": 0.05,
            "max_leverage": 8,
            "max_open_positions": 3,
            "daily_loss_limit_pct": 0.10,
        },
        "ai_engine": {
            "primary_llm": "gemini-2.5-flash",
            "fallback_llm": "claude-sonnet",
            "llm_call_interval_sec": 60,
            "anomaly_trigger_llm": True,
            "max_daily_llm_cost_usd": 5.0,
            "math_weight": 0.6,
            "llm_weight": 0.4,
        },
        "signals": {
            "snapshot_interval_sec": 10,
            "indicators": ["rsi_14", "macd", "bb_20", "ema_9", "ema_21", "ema_50", "atr_14", "vwap"],
            "volume_spike_threshold": 2.0,
            "anomaly_score_threshold": 0.7,
        },
        "pipeline": {
            "zmq_address": "tcp://127.0.0.1:5555",
            "snapshot_file": "/tmp/sentinel_snapshot.json",
        },
        "paper": {
            "starting_balance": 1000.0,
        },
    }

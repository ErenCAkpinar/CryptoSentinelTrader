"""Load configuration from TOML file with defaults and environment overrides."""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Secrets are kept out of config.toml and injected from the environment / .env
_ENV_OVERRIDES = {
    ("exchange", "api_key"): "EXCHANGE_API_KEY",
    ("exchange", "api_secret"): "EXCHANGE_API_SECRET",
    ("ai_engine", "deepinfra_api_key"): "DEEPINFRA_API_KEY",
    ("ai_engine", "openrouter_api_key"): "OPENROUTER_API_KEY",
    ("ai_engine", "brave_api_key"): "BRAVE_API_KEY",
    ("dashboard", "telegram_bot_token"): "TELEGRAM_BOT_TOKEN",
}


def load_config(path: str = "config/config.toml") -> dict:
    """Load config from TOML (or defaults), then apply env var overrides for secrets."""
    if load_dotenv is not None:
        load_dotenv()

    config_path = Path(path)

    if config_path.exists():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    else:
        config = _defaults()

    _apply_env_overrides(config)
    return config


def _apply_env_overrides(config: dict) -> None:
    """Override config values with environment variables when set."""
    for (section, key), env_var in _ENV_OVERRIDES.items():
        value = os.environ.get(env_var)
        if value:
            config.setdefault(section, {})[key] = value


def _defaults() -> dict:
    """Default configuration for development/testing."""
    return {
        "exchange": {
            "primary": "binance_futures_testnet",
            "api_key": "",
            "api_secret": "",
            "symbols": ["BTCUSDT", "ETHUSDT"],
        },
        "risk": {
            "default_risk_per_trade": 0.02,
            "low_confidence_risk": 0.01,
            "high_confidence_risk": 0.05,
            "max_leverage": 5,
            "max_open_positions": 3,
            "daily_loss_limit_pct": 0.05,
        },
        "ai_engine": {
            "primary_llm": "kimi-k2.5",
            "fallback_llm": "deepseek-v4-flash",
            "reasoning_llm": "deepseek-r1",
            "deepinfra_api_key": "",
            "openrouter_api_key": "",
            "brave_api_key": "",
            "llm_call_interval_sec": 120,
            "anomaly_trigger_llm": True,
            "max_daily_llm_cost_usd": 1.50,
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
        "scanner": {
            "enabled": False,
            "scan_interval_sec": 300,
            "min_volume_24h_usdt": 10_000_000,
            "min_score": 55,
            "max_symbols": 10,
            "ohlcv_timeframe": "15m",
            "ohlcv_limit": 100,
            "top_candidates_for_scoring": 30,
            "always_include": ["BTCUSDT", "ETHUSDT"],
            "excluded_symbols": ["USDCUSDT", "BUSDUSDT", "TUSDUSDT", "DAIUSDT", "FDUSDUSDT"],
        },
    }

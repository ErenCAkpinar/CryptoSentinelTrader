# 🛡️ CryptoSentinelTrader

**AI-powered autonomous crypto futures trading system with real-time market surveillance.**

> Continuous market monitoring → Signal detection → AI decision engine → Adaptive risk execution

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![Rust](https://img.shields.io/badge/Rust-1.75+-orange.svg)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: Real-Time Data Ingestion (Rust Core)          │
│  Binance WS │ Bybit WS │ Hyperliquid WS │ News APIs    │
└──────────────────────┬──────────────────────────────────┘
                       │ market_snapshot.json (every 5-15s)
┌──────────────────────▼──────────────────────────────────┐
│  Layer 2: Signal Scanner (Rust Core)                    │
│  Price Tracker │ Volume Engine │ Indicators │ Anomaly   │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│  Layer 3: AI Decision Engine (Python)                   │
│  Pure Math Engine │ Gemini/Claude LLM │ Consensus Voter │
└──────────────────────┬──────────────────────────────────┘
                       │ action: HOLD / ENTER / EXIT / STOP
┌──────────────────────▼──────────────────────────────────┐
│  Layer 4: Execution + Adaptive Risk (Python)            │
│  Order Executor │ Position Manager │ Stop-Loss Guard    │
│  Confidence < 0.4 → 1% risk │ 0.4-0.7 → 3% │ > 0.7 → 5-7% │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│  Layer 5: Feedback Loop                                 │
│  PnL Tracking → Strategy Adjustment → Signal Retrain   │
└─────────────────────────────────────────────────────────┘
```

## Why Two Languages?

| Component | Language | Reason |
|-----------|----------|--------|
| WebSocket feeds, orderbook parsing, indicator calculation | **Rust** | Sub-millisecond latency, zero-cost abstractions, memory safety for 24/7 operation |
| AI analysis, LLM integration, strategy logic, execution | **Python** | Rich ML/AI ecosystem, exchange API libraries (ccxt), rapid prototyping |
| Communication | **JSON over Unix socket / ZeroMQ** | Rust core produces `market_snapshot.json`, Python AI engine consumes it |

## Features

- **24/7 Market Surveillance**: WebSocket-based continuous monitoring — never misses a signal
- **Multi-Exchange Support**: Binance Futures, Bybit, Hyperliquid (extensible)
- **Hybrid Decision Engine**: Pure math (Kelly criterion, technical scoring) + LLM (Gemini/Claude) with weighted consensus
- **Adaptive Risk Management**: Dynamic position sizing based on confidence scores
- **Cost-Optimized LLM Usage**: Math engine handles routine checks; LLM triggered only on anomalies or periodic reviews
- **Anomaly Detection**: Volume spikes, liquidation cascades, black swan alerts
- **Paper Trading First**: Full simulation mode before any real capital

## Quick Start

### Prerequisites

- Python 3.11+
- Rust 1.75+ (with cargo)
- A Binance Futures testnet account

### Installation

```bash
# Clone
git clone https://github.com/ErenCAkpinar/CryptoSentinelTrader.git
cd CryptoSentinelTrader

# Build Rust core engine
cd core_engine
cargo build --release
cd ..

# Setup Python environment
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt

# Configure
cp config/config.example.toml config/config.toml
# Edit config.toml with your API keys (testnet first!)

# Run in paper trading mode
python -m pipeline.main --mode paper
```

## Configuration

```toml
[exchange]
primary = "binance_futures_testnet"
api_key = "your_testnet_key"
api_secret = "your_testnet_secret"

[risk]
default_risk_per_trade = 0.03        # 3%
low_confidence_risk = 0.01           # 1% when confidence < 0.4
high_confidence_risk = 0.05          # 5-7% when confidence > 0.7
max_leverage = 8
max_open_positions = 3
daily_loss_limit_pct = 0.10          # Stop trading after 10% daily loss

[ai_engine]
primary_llm = "gemini-2.5-flash"
fallback_llm = "claude-sonnet"
llm_call_interval_sec = 60           # Routine LLM check every 60s
anomaly_trigger_llm = true           # Immediate LLM call on anomaly
max_daily_llm_cost_usd = 5.0

[signals]
snapshot_interval_sec = 10
indicators = ["rsi_14", "macd", "bb_20", "ema_9", "ema_21", "ema_50", "atr_14", "vwap"]
volume_spike_threshold = 2.0         # 2x average = spike
anomaly_score_threshold = 0.7
```

## Project Status

- [x] Architecture design & JSON schemas
- [ ] Rust core engine: WebSocket feeds
- [ ] Rust core engine: Indicator calculations
- [ ] Rust core engine: Anomaly detection
- [ ] Python AI engine: Math scoring
- [ ] Python AI engine: LLM integration
- [ ] Python execution: Binance Futures testnet
- [ ] Paper trading pipeline
- [ ] Dashboard (web UI)
- [ ] Live trading (after profitable paper results)

## Risk Disclaimer

This software is for educational and research purposes. Cryptocurrency trading involves substantial risk of loss. Never trade with money you cannot afford to lose. Always start with paper trading.

## License

MIT License — see [LICENSE](LICENSE) for details.

## Author

**Eren C. Akpinar** — Computer Engineering Student & Quantitative Trading Enthusiast

- GitHub: [@ErenCAkpinar](https://github.com/ErenCAkpinar)
- Other Projects: [AI_Hedge_Fund](https://github.com/ErenCAkpinar/AI_Hedge_Fund) | [QuantBoard](https://github.com/ErenCAkpinar/QuantBoard)

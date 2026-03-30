# CLAUDE.md — CryptoSentinelTrader

## Project Overview
AI-powered autonomous crypto futures trading system. Rust core engine handles real-time WebSocket data ingestion and signal processing. Python layer handles AI decision-making (math + LLM) and trade execution.

**Goal**: $1,000 → $100,000 in 1 year through hundreds of compound trades with adaptive risk management.

## Architecture

```
Rust Core (core_engine/) → JSON snapshots via ZMQ → Python AI Engine (ai_engine/) → Execution (execution/)
```

- **Layer 1**: Rust WebSocket feeds (Binance, Bybit, Hyperliquid) — continuous 24/7
- **Layer 2**: Rust signal processor — produces `market_snapshot.json` every 5-15 seconds
- **Layer 3**: Python AI decision engine — MathEngine (always) + LLM (on anomaly/periodic)
- **Layer 4**: Python executor — adaptive risk, paper/live trading via ccxt
- **Layer 5**: Feedback loop — PnL tracking → strategy adjustment

## Tech Stack
- **Rust**: tokio (async), tokio-tungstenite (WebSocket), serde (JSON), zmq (IPC)
- **Python**: ccxt (exchanges), pyzmq (IPC), google-generativeai (Gemini), anthropic (Claude)
- **Communication**: Rust → Python via ZeroMQ PUB/SUB (tcp://127.0.0.1:5555)

## Build & Run Commands

```bash
# Rust core
cd core_engine && cargo build --release
cd core_engine && cargo test
cd core_engine && cargo run

# Python
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -v
python -m pipeline.main --mode paper

# Both together
make run-all
```

## Key Files

| File | Purpose | Status |
|------|---------|--------|
| `core_engine/src/main.rs` | Rust entry point, struct definitions | ✅ Done |
| `core_engine/src/feeds/binance.rs` | Binance Futures WebSocket feed | ✅ Done |
| `core_engine/src/signals/processor.rs` | Tick → Snapshot conversion, RSI, EMA | ✅ Basic |
| `core_engine/src/indicators/calculator.rs` | MACD, BB, ATR, VWAP | ❌ TODO |
| `core_engine/src/anomaly/detector.rs` | Anomaly detection | ❌ TODO |
| `ai_engine/decision_engine.py` | MathEngine + LLMEngine + Consensus | ✅ Done |
| `execution/executor.py` | Paper/live trade execution | ✅ Done |
| `pipeline/main.py` | Main orchestrator | ✅ Done |

## Current TODO Priority

1. **Rust indicators**: Implement MACD(12,26,9), Bollinger Bands(20,2), ATR(14), VWAP in `core_engine/src/indicators/calculator.rs`
2. **Rust anomaly**: Volume z-score, liquidation cascade detection, flash crash in `core_engine/src/anomaly/detector.rs`
3. **LLM integration**: Wire up Gemini API in `ai_engine/decision_engine.py` LLMEngine.analyze()
4. **Bybit feed**: Add Bybit WebSocket feed in `core_engine/src/feeds/`
5. **Hyperliquid feed**: Add Hyperliquid WebSocket feed
6. **Orderbook feed**: Separate orderbook depth stream for imbalance detection
7. **Dashboard**: Simple web UI showing live snapshots, decisions, PnL

## Adaptive Risk Model

```
Confidence < 0.4  → 1% risk per trade, 2x leverage
Confidence 0.4-0.7 → 3% risk per trade, 3-5x leverage
Confidence > 0.7  → 5-7% risk per trade, 5-8x leverage
```

Confidence = weighted average of MathEngine score (60%) + LLM confidence (40%).

## JSON Schemas

**market_snapshot.json** (Rust → Python): Contains price, volume, orderbook, indicators, funding_rate, anomaly_detection, market_regime fields.

**decision_output.json** (Python internal): Contains decision (action + confidence), math_engine_output, llm_engine_output, consensus, risk_parameters.

## Code Style
- Rust: standard rustfmt, descriptive variable names
- Python: black formatter, ruff linter, type hints preferred
- All comments in English
- User communication in Turkish (Eren speaks Turkish)

## Important Notes
- NEVER put real API keys in code — always use config.toml (gitignored)
- Paper trading mode is DEFAULT — live mode requires explicit --mode live flag
- Daily loss limit is 10% — bot stops trading after hitting this
- LLM calls are cost-controlled: max $5/day, only on anomalies or every 60s
- Binance Futures TESTNET first, real money only after proven paper results

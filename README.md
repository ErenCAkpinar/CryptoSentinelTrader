# 🛡️ CryptoSentinelTrader

**A Python + Rust research system for crypto futures trading experiments.**

A Rust engine streams live Binance futures market data and computes indicators and anomaly scores. A Python pipeline scores each market snapshot, can ask an LLM ensemble for a second opinion, and manages paper positions under hard risk limits.

> **Status:** research project, paper trading only. Live trading is not enabled, and this README makes no performance claims.

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![Rust](https://img.shields.io/badge/Rust-stable-orange.svg)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## What works today

Checked from a clean clone on 25 September 2026 (Python 3.11, Rust 1.95):

- `cargo build` builds the Rust workspace, and `cargo test` passes all 26 Rust unit tests (indicators and anomaly detection).
- The paper pipeline starts the Rust engine, receives live Binance futures snapshots over ZeroMQ, makes a decision for each one and opens/closes paper positions with stop-loss, take-profit and fees.
- Without LLM API keys the ensemble falls back to a neutral vote, so decisions come from the math engine alone.
- The Python unit tests for the math engine and the opportunity scanner pass.

## Architecture

The design has seven layers. Not all of them are built yet; the table shows where each one stands.

| Layer | Responsibility | Code | Status |
|---|---|---|---|
| 0 · MCP bridges | TradingView and whale-data connectors for AI tools | `mcp/` | Experimental (placeholder data) |
| 1 · Ingestion | Binance futures WebSocket streams, REST warm-up of recent candles | `rust/sentinel-ingestion/src/feeds` | Implemented |
| 2 · Signal processing | RSI, EMA, MACD, Bollinger Bands, ATR, ADX, VWAP; price/volume/order-flow anomaly scores; snapshot publishing over ZeroMQ | `rust/sentinel-ingestion/src/{indicators,anomaly,signals}` | Implemented, unit-tested |
| 3 · Decision engine | Math scoring engine + optional LLM ensemble (Gemini 2.5 Flash/Pro and Claude, weighted consensus); HMM regime model and confidence calibration | `python/sentinel/ai_engine` | Math engine implemented and tested; LLM ensemble implemented (needs API keys); regime and calibration experimental |
| 4 · Risk & execution | Paper executor (sizing, leverage cap, SL/TP, fees), kill switch, circuit breaker, ccxt exchange client | `python/sentinel/risk` | Implemented for paper mode; the Rust executor is a placeholder |
| 5 · Wallet intelligence | Polymarket whale discovery, scoring and clustering | `python/sentinel/wallet_intel` | Experimental (partly mocked) |
| 6 · Monitoring | Textual terminal dashboard and Telegram bot | `python/sentinel/dashboard` | Standalone prototypes; not yet reading the executor's position file |

Other modules:

- `python/sentinel/strategies/scanner.py`: opportunity scanner that chooses the active symbol list (unit-tested)
- `python/sentinel/data/backtest_engine.py`: backtester for the math engine
- `python/sentinel/strategies/latency_arb.py`, `spread_capture.py`: early strategy sketches
- `track_record/`: read-only exporter and static dashboard that publish a bot's paper/testnet record (currently used for [BreakoutBot](https://breakoutbot.dev))

Data flow:

```
Binance WebSocket ──► Rust ingestion ──► indicators + anomaly scores ──► snapshot (ZeroMQ PUB, tcp://127.0.0.1:5555)
                                                                              │
Python pipeline ◄─────────────────────────────────────────────────────────────┘
   └─► circuit breaker ──► math engine (+ optional LLM ensemble) ──► paper executor ──► kill switch / risk limits
```

### Why two languages?

| Part | Language | Reason |
|---|---|---|
| Market-data streams, indicators, anomaly detection | **Rust** | Long-running async service (tokio) with predictable memory use |
| Decision logic, LLM calls, risk and execution | **Python** | ML/LLM libraries and exchange clients (ccxt), fast iteration |
| Bridge | **ZeroMQ** | Rust publishes JSON snapshots; Python subscribes |

## Run locally

You need Python 3.11+ and a stable Rust toolchain. ZeroMQ is compiled automatically by the Rust build (a C++ compiler is required, e.g. Xcode Command Line Tools on macOS).

```bash
git clone https://github.com/ErenCAkpinar/CryptoSentinelTrader.git
cd CryptoSentinelTrader

# Rust engine (built from the workspace root)
cargo build --release

# Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Config and runtime folder
cp config/config.example.toml config/config.toml   # API keys are only needed for the LLM ensemble
mkdir -p data

# Paper-trading pipeline (starts the Rust engine as a subprocess)
PYTHONPATH=python python -m sentinel.main --mode paper
```

The Makefile wraps the same steps: `make build-rust`, `make setup-python`, `make run-paper`, `make test`.

## Tests

```bash
cargo test --workspace   # 26 Rust unit tests
pytest tests/ -q         # math engine and opportunity scanner
```

## Risk controls

Enforced in paper mode by the Python executor, circuit breaker and kill switch:

- Daily loss limit (5% in `config.example.toml`), at most 3 open positions, 5× leverage
- One position per symbol and a 15-minute cooldown after a position closes
- Risk per trade by confidence tier: 1.5% (low), 3% (medium), 5% (high), replaced by a rolling Kelly fraction once enough closed trades exist
- Kill switch that halts the pipeline

`config/risk_limits.yaml` holds the target limits for later versions; the code does not load it yet.

## Known limitations

- Only Binance futures data is wired in; Bybit and Hyperliquid are planned.
- Stopping the pipeline closes paper positions, but background loops can keep the process alive. Stop it manually if it does not exit.
- The LLM ensemble needs API keys; without them, decisions come from the math engine only.
- The `[ai_engine]` model settings in `config.example.toml` (DeepInfra/OpenRouter) are not used yet; the ensemble currently calls Gemini and Claude directly.
- The old LLM engine tests are skipped until they are rewritten for the current ensemble.

## Roadmap

- [ ] Bybit and Hyperliquid feeds
- [ ] Order-book depth feed (currently a TODO in the signal processor)
- [ ] Rust executor
- [ ] Tests for the LLM ensemble
- [ ] Clean shutdown of all background loops
- [ ] Longer paper-trading evaluation before any live trading

## Risk Disclaimer

This software is for educational and research purposes. Cryptocurrency trading involves substantial risk of loss. Never trade with money you cannot afford to lose. Always start with paper trading.

## License

MIT License — see [LICENSE](LICENSE) for details.

## Author

**Eren C. Akpinar** — Computer Engineering Student & Quantitative Trading Enthusiast

- GitHub: [@ErenCAkpinar](https://github.com/ErenCAkpinar)
- Other projects: [BreakoutBot](https://github.com/ErenCAkpinar/BreakoutBot) | [AI_Hedge_Fund](https://github.com/ErenCAkpinar/AI_Hedge_Fund)

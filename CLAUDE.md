# CryptoSentinelTrader — Claude Code Context

## Proje Hakkında
Hybrid Python+Rust AI-powered crypto trading bot. Küçük sermayeden agresif bileşik büyüme hedefliyor.

## Mimari: 7-Layer Sentinel Architecture
- **Layer 0: MCP Bridge** → TradingView, Exchange, Polymarket connectors.
- **Layer 1: Rust Ingestion** → WebSocket feeds, orderbook aggregator, whale monitor.
- **Layer 2: Signal Processing** → Indicators, anomaly detection, regime classification.
- **Layer 3: AI Decision Engine** → Multi-LLM ensemble (Claude + Gemini + GPT), sentiment.
- **Layer 4: Adaptive Risk & Execution** → Position management, SL/TP, Kelly sizing.
- **Layer 5: Wallet Intelligence** → Polymarket whale tracking, cluster detection.
- **Layer 6: Dashboard & Monitoring** → Textual TUI, Telegram bot.

## Kodlama Kuralları
- **Python**: Type hints zorunlu, async/await tercih et, pydantic modelleri kullan.
- **Rust**: tokio async runtime, serde JSON serialization, descriptive variables.
- Tüm trade kararları loglanmalı (decision audit trail).
- Paper mode varsayılan — live mode için 3 güvenlik kapısı gerekli.

## Kritik Dosyalar
- `config/risk_limits.yaml` → Asla gevşetme.
- `python/sentinel/risk/kill_switch.py` → Her zaman çalışır durumda olmalı.
- `.env` → API key'ler, asla commit etme.

## Test
- `pytest` ile unit test, her strategy modülü için.
- Paper trading integration test zorunlu live'dan önce.

## Mevcut Entegrasyonlar
- Binance Futures (Testnet & Live)
- Bybit (In progress)
- Hyperliquid (In progress)
- Polymarket (Planned)
- GCP e2-micro VM (Production)

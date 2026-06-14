# CryptoSentinelTrader — Claude Code Master Prompt

## Proje Kimliği
- **Proje Adı:** CryptoSentinelTrader
- **GitHub:** ErenCAkpinar/CryptoSentinelTrader
- **Hedef:** Küçük sermayeden agresif bileşik büyüme — AI-destekli kripto trading sistemi
- **Stack:** Python (AI karar motoru) + Rust (düşük gecikmeli veri alma & execution) + MCP entegrasyonları
- **Ortam:** MacBook Air M2 geliştirme → GCP e2-micro production

---

## Mimari Vizyon: 7-Layer "Sentinel Architecture"

Mevcut 5 katmanlı mimarini şu şekilde genişlet:

```
Layer 0: MCP Bridge Layer (YENİ)
├── TradingView MCP connector (tradesdontlie/tradingview-mcp tarzı)
├── Exchange MCP connectors (Binance, Bybit WebSocket)
└── Polymarket MCP connector (CLOB API + whale feed)

Layer 1: Rust Data Ingestion (MEVCUT — güçlendir)
├── WebSocket multiplexer (Binance, Bybit, OKX spot + futures)
├── Polymarket activity stream (wss://ws-live-data.polymarket.com)
├── On-chain whale monitor (Solana, ETH, Polygon)
└── Orderbook aggregator (L2 depth, spread hesaplama)

Layer 2: Signal Processing Engine (MEVCUT — genişlet)
├── Latency arbitrage detector (exchange vs Polymarket fiyat farkı)
├── Whale convergence detector (3+ whale aynı pozisyon = sinyal)
├── Spread engine (bid-ask spread capture fırsatları)
├── Momentum ribbon (EMA cascade: 8, 13, 21, 34)
└── Volatility regime classifier (HMM-based)

Layer 3: AI Decision Engine (MEVCUT — yükselt)
├── Multi-LLM ensemble forecaster
│   ├── Claude Sonnet (ağırlık: 40%) — primary reasoning
│   ├── Gemini Flash (ağırlık: 35%) — hızlı analiz
│   └── GPT-4o-mini (ağırlık: 25%) — cross-validation
├── Sentiment agent (Reddit, Twitter, news RSS)
├── Confidence calibration (Platt scaling)
└── Kelly criterion position sizer (7 çarpan: confidence, drawdown, timeline, volatility, regime, category, liquidity)

Layer 4: Adaptive Risk & Execution (MEVCUT — güçlendir)
├── Execution strategies: Simple, TWAP, Iceberg, Adaptive
├── Dynamic stop-loss & trailing stops
├── Circuit breaker (daily loss limit, VaR/CVaR)
├── Kill switch (tek komutla tüm pozisyonları kapat)
└── Paper/Live mode toggle (3 bağımsız güvenlik kapısı)

Layer 5: Wallet Intelligence (YENİ — "Phantom Copy" modülü)
├── Top 100 wallet auto-discovery (Polymarket leaderboard scraper)
├── Multi-dimensional wallet scoring
│   ├── Profitability (30%)
│   ├── Timing skill (20%)
│   ├── Low slippage (15%)
│   ├── Consistency (15%)
│   ├── Market selection (10%)
│   └── Recency (10%)
├── Whale cluster detection (convergence alert)
├── Copy-trade simulator (backtesting with slippage model)
└── Selective mirror execution (filtreli copy trading)

Layer 6: Dashboard & Monitoring (YENİ)
├── Terminal TUI (rich/textual library)
├── Real-time PnL tracker
├── Strategy performance heatmap
├── Whale activity stream
└── Telegram bot entegrasyonu (start/stop, alerts, status)
```

---

## Faz 1: Temel Altyapı (İlk 2 Hafta)

### Görev 1.1 — Proje yapısını oluştur
```
CryptoSentinelTrader/
├── CLAUDE.md                    # Bu dosya — Claude Code konteksti
├── pyproject.toml               # Python dependencies (uv/poetry)
├── Cargo.toml                   # Rust workspace
├── rust/
│   ├── sentinel-ingestion/      # WebSocket data feeds
│   │   ├── src/
│   │   │   ├── main.rs
│   │   │   ├── ws_binance.rs
│   │   │   ├── ws_polymarket.rs
│   │   │   ├── orderbook.rs
│   │   │   └── whale_monitor.rs
│   │   └── Cargo.toml
│   └── sentinel-executor/       # Düşük gecikmeli order execution
│       ├── src/
│       │   ├── main.rs
│       │   ├── alpaca_client.rs
│       │   ├── polymarket_client.rs
│       │   └── risk_gate.rs
│       └── Cargo.toml
├── python/
│   ├── sentinel/
│   │   ├── __init__.py
│   │   ├── ai_engine/
│   │   │   ├── ensemble.py      # Multi-LLM forecaster
│   │   │   ├── sentiment.py     # Sentiment agent
│   │   │   ├── calibration.py   # Platt scaling
│   │   │   └── prompts.py       # Trading analysis prompts
│   │   ├── strategies/
│   │   │   ├── latency_arb.py   # Latency arbitrage
│   │   │   ├── spread_capture.py # Market making
│   │   │   ├── whale_copy.py    # Copy trading
│   │   │   ├── momentum.py      # Trend following
│   │   │   └── mean_reversion.py
│   │   ├── risk/
│   │   │   ├── kelly.py         # Kelly criterion + 7 multipliers
│   │   │   ├── circuit_breaker.py
│   │   │   ├── var_cvar.py
│   │   │   └── kill_switch.py
│   │   ├── wallet_intel/
│   │   │   ├── discovery.py     # Leaderboard scraper
│   │   │   ├── scorer.py        # Multi-dimensional scoring
│   │   │   ├── cluster.py       # Convergence detection
│   │   │   └── mirror.py        # Copy execution
│   │   ├── data/
│   │   │   ├── market_data.py
│   │   │   ├── polymarket_api.py
│   │   │   └── exchange_feeds.py
│   │   └── dashboard/
│   │       ├── tui.py           # Terminal UI (textual)
│   │       └── telegram_bot.py
│   └── tests/
├── mcp/
│   ├── tradingview-bridge/      # TradingView MCP connector
│   │   ├── package.json
│   │   └── src/
│   │       └── server.js
│   └── sentinel-mcp/            # CryptoSentinel'i MCP olarak expose et
│       └── server.py
├── config/
│   ├── strategies.yaml
│   ├── risk_limits.yaml
│   ├── wallets_watchlist.yaml
│   └── exchanges.yaml
├── .env.example
├── docker-compose.yml
└── README.md
```

### Görev 1.2 — CLAUDE.md dosyasını yaz
Bu dosyayı projenin kök dizinine koy. Claude Code her çalıştığında otomatik okur:

```markdown
# CryptoSentinelTrader — Claude Code Context

## Proje Hakkında
Hybrid Python+Rust AI-powered crypto trading bot. Küçük sermayeden agresif bileşik büyüme hedefliyor.

## Mimari
- Rust: WebSocket ingestion + low-latency execution
- Python: AI decision engine + strategies + dashboard
- MCP: TradingView ve exchange bağlantıları

## Kodlama Kuralları
- Python: Type hints zorunlu, async/await tercih et, pydantic modelleri kullan
- Rust: tokio async runtime, serde JSON serialization
- Her modül kendi error type'ına sahip olmalı
- Tüm trade kararları loglanmalı (decision audit trail)
- Paper mode varsayılan — live mode için 3 güvenlik kapısı gerekli

## Kritik Dosyalar
- config/risk_limits.yaml → Asla gevşetme
- python/sentinel/risk/kill_switch.py → Her zaman çalışır durumda olmalı
- .env → API key'ler, asla commit etme

## Test
- pytest ile unit test, her strategy modülü için
- Paper trading integration test zorunlu live'dan önce

## Mevcut Entegrasyonlar
- Alpaca (paper trading) — çalışıyor
- GCP e2-micro VM — production
- GitHub: ErenCAkpinar/CryptoSentinelTrader
```

### Görev 1.3 — Temel bağımlılıkları kur
```bash
# Python
pip install anthropic google-generativeai openai ccxt websockets aiohttp \
  pydantic pyyaml python-dotenv rich textual python-telegram-bot \
  numpy scipy pandas scikit-learn hmmlearn arch statsmodels

# Rust
cargo init rust/sentinel-ingestion
cargo add tokio serde serde_json tokio-tungstenite futures-util chrono uuid
```

---

## Faz 2: Wallet Intelligence & Whale Tracking (Hafta 3-4)

### Görev 2.1 — Polymarket Whale Discovery
`dylanpersonguy/Fully-Autonomous-Polymarket-AI-Trading-Bot` referans alınarak:

```python
# python/sentinel/wallet_intel/discovery.py

"""
Polymarket Leaderboard'dan top 100 wallet'ı keşfet.
Her wallet için: PnL, win rate, volume, aktif market sayısı.

Pipeline:
1. Leaderboard → Top 50 profit + Top 50 volume
2. Her wallet için trade history çek
3. Multi-dimensional scoring uygula
4. Whale cluster detection (3+ whale aynı market = ALERT)
5. SQLite'a kaydet, her 6 saatte yenile
"""
```

### Görev 2.2 — Copy Trade Simulator
Gerçek para koymadan önce, geçmiş whale trade'lerini simüle et:
- Configurable slippage model (0.1-0.5%)
- Delay simulation (1-5 saniye gecikme)
- Her whale için ayrı backtest raporu

### Görev 2.3 — Real-time Whale Stream
```rust
// rust/sentinel-ingestion/src/whale_monitor.rs
// Polymarket WebSocket: wss://ws-live-data.polymarket.com
// Target wallet'ları filtrele, Python'a ZeroMQ ile ilet
```

---

## Faz 3: Multi-LLM AI Engine (Hafta 5-6)

### Görev 3.1 — Ensemble Forecaster
```python
# python/sentinel/ai_engine/ensemble.py

"""
3 LLM paralel çalıştır, her biri bağımsız olasılık tahmini üretir.

Claude Sonnet: %40 ağırlık — derin reasoning, web search ile
Gemini Flash: %35 ağırlık — hızlı analiz
GPT-4o-mini: %25 ağırlık — cross-validation

Aggregation: trimmed_mean (en yüksek ve en düşük atılır)

Prompt şablonu:
"Analyze this crypto market: {market_description}
Current price: {price}, 24h change: {change_24h}
Volume: {volume}, Open interest: {oi}

Technical indicators:
- RSI(14): {rsi}
- MACD: {macd_line}, Signal: {signal_line}
- Bollinger: Upper={bb_upper}, Lower={bb_lower}
- EMA Ribbon: {ema_8}/{ema_13}/{ema_21}/{ema_34}

Whale activity: {whale_summary}
Sentiment score: {sentiment}

Respond ONLY with JSON:
{
  "direction": "LONG" | "SHORT" | "NEUTRAL",
  "confidence": 0.0 to 1.0,
  "timeframe": "5m" | "1h" | "4h" | "1d",
  "key_factors": ["factor1", "factor2"],
  "risk_level": "LOW" | "MEDIUM" | "HIGH"
}"
"""
```

### Görev 3.2 — Confidence Calibration
Platt scaling ile LLM confidence'larını kalibre et. Tarihsel tahmin vs gerçekleşen sonuç verisiyle eğit.

---

## Faz 4: Strategy Modülleri (Hafta 7-8)

### Görev 4.1 — Latency Arbitrage (Polymarket focus)
```python
# Binance/Bybit spot fiyatı vs Polymarket 5-min BTC Up/Down
# Lag >0.3% tespit edilince, <100ms içinde pozisyon aç
# Risk: trade başına max %0.5, günlük max %2
```

### Görev 4.2 — Spread Capture (Market Making)
```python
# Bid-ask spread'in her iki tarafına limit order koy
# Inventory risk yönetimi (tek taraflı birikmeyi engelle)
# Dynamic spread ayarı (volatiliteye göre)
```

### Görev 4.3 — Momentum + Mean Reversion Hybrid
Mevcut quant math modülünü (HMM, GARCH, Kalman) kullan:
- Regime detection ile strateji seçimi
- Bullish regime → momentum follow
- Mean-reverting regime → Bollinger bounce
- High volatility → spread widen, position size küçült

---

## Faz 5: MCP Entegrasyonları (Hafta 9-10)

### Görev 5.1 — TradingView MCP Bridge
`tradesdontlie/tradingview-mcp` repo'sunu referans al:

```bash
# TradingView Desktop'ı debug port ile başlat:
# Mac: open -a "TradingView" --args --remote-debugging-port=9222

# MCP server'ı kur:
cd mcp/tradingview-bridge
npm init -y
npm install ws node-fetch
```

MCP araçları:
- `tv_symbol_change` → Sembol değiştir
- `tv_pine_inject` → Pine Script yaz ve enjekte et
- `tv_pine_compile` → Derle, hata varsa düzelt
- `tv_screenshot` → Chart screenshot al, Claude'a gönder analiz için
- `tv_data_get_study_values` → İndikatör değerlerini oku
- `tv_morning_brief` → Watchlist tara, session bias üret

### Görev 5.2 — Sentinel MCP Server
CryptoSentinelTrader'ı kendisi bir MCP server olarak expose et:
```python
# Claude Desktop/Code'dan şunu yazabilirsin:
# "CryptoSentinel, BTC için whale aktivitesini göster"
# "Son 24 saatte en karlı strateji hangisi?"
# "Paper mode'da ETHUSDT short aç, %2 stop-loss ile"
```

---

## Faz 6: Dashboard & Monitoring (Hafta 11-12)

### Görev 6.1 — Terminal TUI
`textual` kütüphanesi ile Bloomberg-tarzı terminal:
```
┌─ PnL: +$237.97 ──── Win Rate: 56.6% ──── Active: 3 ─┐
│                                                         │
│  [Whale Stream]     [Positions]      [Strategy Stats]   │
│  🐋 0xab..cd BUY   BTCUSDT LONG     Latency Arb: +$89  │
│  🐋 0xef..12 SELL  ETHUSDT SHORT    Spread Cap: +$142   │
│  🐋 0x34..56 BUY   SOLUSDT LONG     Whale Copy: +$7     │
│                                                         │
│  [Signal Feed]                [Risk Status]             │
│  ⚡ BTC lag detected: 0.4%   Daily P&L: +$237          │
│  📊 3 whales converged ETH   Max DD: -$45 (1.8%)       │
│  🎯 Ensemble: LONG 78%       Circuit: ✅ OK            │
└─────────────────────────────────────────────────────────┘
```

### Görev 6.2 — Telegram Bot
```python
# Komutlar:
# /status → Mevcut pozisyonlar + PnL
# /whale → Son whale hareketleri
# /kill → Tüm pozisyonları kapat (kill switch)
# /mode paper|live → Mod değiştir
# /pnl → Günlük/haftalık/aylık performans
```

---

## Önemli Referans Repolar

| Repo | Ne İçin Kullan |
|------|---------------|
| `tradesdontlie/tradingview-mcp` | TradingView MCP entegrasyonu, Pine Script otomasyon |
| `dylanpersonguy/Fully-Autonomous-Polymarket-AI-Trading-Bot` | Whale tracking, multi-LLM ensemble, risk management |
| `alsk1992/CloddsBot` | 118+ strateji, multi-platform trading, MCP server |
| `HyperBuildX/Polymarket-Trading-Bot-Rust` | Rust copy trading, WebSocket feed, dashboard |
| `discountry/polymarket-trading-bot` | Basit Python Polymarket bot, CLAUDE.md örneği |
| `dev-protocol/Polymarket-Trading-Bot-with-Synth-AI` | Latency arbitrage Rust implementation |
| `atilaahmettaner/tradingview-mcp` | 30+ teknik analiz aracı, backtesting, sentiment |
| `hummingbot/hummingbot` | Market making framework, MCP server |

---

## Güvenlik Kuralları (ASLA Değiştirme)

1. **Paper mode varsayılan** — Live mode için 3 bağımsız onay gerekli
2. **Trade başına max %1 sermaye** — Kelly önerisi bile olsa
3. **Günlük max %5 kayıp** → Circuit breaker tetikle
4. **Kill switch her zaman aktif** — Telegram /kill komutu
5. **API key'ler sadece .env'de** — Asla hardcode, asla commit
6. **Her trade kararı loglanır** — Audit trail zorunlu
7. **LLM API key'leri doğrulanır** — Sistem başlangıcında kontrol et (önceki boş env var hatası tekrarlanmasın!)

---

## Claude Code'a İlk Komut

Projeyi başlatmak için terminal'de şunu çalıştır:

```
claude "Bu projenin CLAUDE.md dosyasını oku. Sonra şu adımları takip et:
1. Proje yapısını yukarıdaki şemaya göre oluştur
2. pyproject.toml ve Cargo.toml'ı kur
3. config/ altındaki YAML dosyalarını örnek değerlerle doldur
4. python/sentinel/ai_engine/ensemble.py'yi implement et — 3 LLM paralel çalışsın
5. python/sentinel/risk/circuit_breaker.py'yi implement et
6. python/sentinel/risk/kill_switch.py'yi implement et
7. Basit bir paper trading loop yaz: veri al → sinyal üret → karar ver → logla
8. pytest ile temel testleri yaz
Her adımı tamamladıkça bana göster."
```

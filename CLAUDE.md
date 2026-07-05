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

## Sunucu Test Sonuçları — 15 Günlük Canlı Test (31 May → 15 Haz 2026)
GCP VM'de iki bot paralel test edildi (`journalctl -u breakoutbot` / `-u breakoutbot-test`).

**MAIN bot (`breakoutbot.service`) — agresif hep-long breakout, REJİM FİLTRESİZ:**
- $995 → $796.88 = **−20.3%**, PEAK_DD_LIMIT (−20%) kill switch tetiklendi.
- 60 FULL trade, WR %50 AMA ort. kazanç +$5.35 / ort. kayıp −$10.34 → **R:R ≈ 0.52 (yapısal kayıp)**.
- En kötü semboller: ORDI −$48, NEAR −$28. Sebep: choppy/bear piyasada hep-long açmak.
- 14 Haz'da Binance Testnet live orders'a geçti (23 sembol, 3× isolated); içsel paper bakiye testnet hesabından ayrı.

**TEST bot (`breakoutbot-test.service`) — regime-aware simülasyon:**
- $1000 → $1000.37 = **düz**. Dönem boyunca rejim hiç BULL olmadı → tüm long'lar throttle→0.
- Sadece 2 MR (mean-reversion) trade. Stabil, crash-loop yok.
- **Çıkarım: Rejim filtresi MAIN'i mahveden −%20'yi tamamen önlerdi ama BULL eşiği çok katı → neredeyse hiç trade yok.**

**Altyapı (Hetzner):** Server `breakoutbot` = CX23, IP 157.180.117.112, proje `money_tree`. Kaynak bol — CPU genelde <%5, RAM/disk/network rahat. **Botun sorunları kaynak değil strateji+wiring kaynaklı.** Not: crash-loop sırasında CPU grafiğinde ~%20 spike görülür (her 5 dk'da testnet reconnect + 23 sembol setup); fix çalışınca CPU flat'e döner = iyi gösterge.

**Sunucu deploy yapısı (prod kaynak repo'da DEĞİL, sadece VM'de):**
- Script: `paper_bb.py` — iki ayrı kopya, ortak venv `/root/BreakoutBot/.venv`.
- MAIN: `breakoutbot.service` → `/root/BreakoutBot/paper_bb.py --testnet --resume` (Binance testnet emirleri, REJİMSİZ). `Restart=always RestartSec=15` → crash-loop'un sebebi bu.
- TEST: `breakoutbot-test.service` → `/root/BreakoutBot-test/paper_bb.py --resume` (lokal sim, REJİM-AWARE).
- İki dosyanın farkı = rejim mantığı: `diff /root/BreakoutBot/paper_bb.py /root/BreakoutBot-test/paper_bb.py`.
- `--testnet` flag'i paper_bb.py içinde tanımlı; state dosyası `state_paper.json` (her dir kendi WorkingDirectory'sinde).

**KRİTİK MİMARİ GERÇEK:** `-test` (`/root/BreakoutBot-test/paper_bb.py`) eski MAIN'in değil, **bir sonraki neslidir** — zaten içeriyor: 4h rejim sınıflandırıcı (`regime.py`, BTC 200-MA, long sadece BULL'da `LONG_SIZE_MULT`), risk-bazlı dinamik sizing (`s.full_notional`), −%7 equity throttle (`EQUITY_THROTTLE_DD` ×0.5), MR sleeve (`mean_reversion.py`, NEUTRAL'da netting-guard'lı), MR testnet mirror. Bu kod MAIN −%20 kanarken DÜZ hayatta kaldı. → **Fix #3 = elle port DEĞİL, `-test` kodunu `--testnet` ile MAIN yapmak (promote test→main).** Destek modülleri (`regime.py`, `mean_reversion.py`, `config.py:MR_ENABLED`) sadece `-test` dizininde.

**`-test` = baştan VALİDE EDİLMİŞ strateji (Faz 4c), eski MAIN'den çok ileri:**
- **8 küratörlü coin** (INJ POL LDO SOL AVAX NEAR UNI ADA) — eski 23'ün edge tutan 8'i. Atılanlar (ORDI WIF AAVE JUP PENDLE SUI APT FET) MAIN'i kanatan tam da bunlardı.
- **Risk-bazlı sizing** `RISK_PER_TRADE_USD=$10` → her SL ~$10 sabit (eski flat sizing FET'i ~$14, NEAR'ı ~$9 risk ettiriyordu = R:R asimetrisinin kök sebebi). Coeff'ler config.py'de: `SL_TEST_ATR/SL_FULL_ATR/TP1_ATR/TP2_ATR/TRAIL_ATR`.
- **DD**: PEAK_DD −15% (eski −20%) + −7% throttle ×0.5.
- **Backtest**: 90d 8/8 kârlı PF 3.29 MaxDD −3.25%; 240d ayı −$16/ay MaxDD −9.66% (hard-stop yok). Short sleeve test edildi, edge yok → `SHORT_ENABLED=False`.
- **config.py'de secret/testnet bağı YOK** → güvenle takas edilir; testnet tamamen `paper_bb.py`+`secrets_local.py`'de. `secrets_local.py` sadece MAIN dizininde.

**Açık buglar / TODO:**
1. ✅ **DONE (15 Haz):** Crash-loop düzeltildi — `override.conf` ile `Restart=on-failure` + `RestartPreventExitStatus=1`. MAIN durduruldu (state peak_dd=−20.3% kilitli).
2. ✅ **#2+#3 birleşti → tek iş: PROMOTE test→main.** Risk sizing + coin küratörlüğü R:R'yi (#2) zaten çözüyor; rejim filtresi (#3) zaten kodlu. ⚠️ Valide coeff'leri ELLE KURCALAMA (PF 3.29 backtest'i bozar).
3. ✅ **DONE (15 Haz):** Promote tamamlandı. `-test`'in tüm .py'leri MAIN dizinine kopyalandı (secrets/venv MAIN'de kaldı, servis tanımı değişmedi — hâlâ `/root/BreakoutBot` + `--testnet --resume`). Eski −20% state `state_paper.json.locked_20260615`'e taşındı, temiz $1000/8-coin ile başladı. Servis `active (running)`, crash-loop yok. `-test` dizini/servisi durmuş halde dokunulmadan duruyor (yedek/deney). Yedek: `/root/BreakoutBot_PROMOTE_BACKUP_20260615_1053.tar.gz`.

**Canlı durum (15 Haz sonrası):** MAIN artık Faz 4c stratejisi çalıştırıyor — 8 coin, rejim-aware, risk-bazlı sizing, testnet live orders, fresh $1000 takip. Bir sonraki adım: birkaç gün/hafta testnet'te izle, R:R ve trade sıklığını logdan doğrula. Valide coeff'lere dokunma.

## Track Record Panosu — Ürün Katmanı (`track_record/`, 15 Haz)
İlk ürün MVP'si: canlı botun GERÇEK testnet performansını gösteren **read-only şeffaflık panosu**. Konumlanma: vanity equity grafiği DEĞİL, **risk disiplini vitrini** ("patlamayan sistem"). Moat = doğrulanabilir şeffaflık + risk disiplini; egzotik alfa iddiası YOK (repo'daki Hurst/kurtosis placeholder).
- **Read-only:** trading koduna (`paper_bb.py` vb.) DOKUNMAZ; sadece `paper_bb.log`+`state_paper.json`+`config.py` okur.
- Bileşenler: `models.py` (pydantic sözleşme `track_record.json`), `exporter.py` (log/state parser → JSON), `web/` (bağımlılıksız statik pano + canvas equity grafiği), `telegram_broadcaster.py` (düz HTTP, idempotent public yayın), `deploy/` (systemd timer+nginx), `sample/` (lokal test fixture).
- Her yüzeyde zorunlu uyarı: testnet/gerçek-para-değil, getiri vaadi yok, tavsiye değil.
- **Phase 2 (yapılmadı):** Whop/Telegram ücretli abonelik ($25–50/ay, rejim+Kelly-sized giriş+risk panosu füzyonu). Bu MVP ~30 gün ücretsiz güven/dönüşüm testi. Plan: `~/.claude/plans/tamam-bu-do-ru-soru-fluttering-journal.md`.

## Mevcut Entegrasyonlar
- Binance Futures (Testnet & Live)
- Bybit (In progress)
- Hyperliquid (In progress)
- Polymarket (Planned)
- GCP e2-micro VM (Production)

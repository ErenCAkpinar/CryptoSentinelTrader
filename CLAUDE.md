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

## BreakoutBot — Faz 5: Ölçüm Düzeltmesi + Çıkış Yapısı Deneyleri (29 Tem 2026)

⚠️ **BreakoutBot artık kendi private repo'sunda:** `github.com/ErenCAkpinar/BreakoutBot` (lokal: `~/Downloads/BreakoutBot`). Kaynak kod orada versiyonlu; VM hâlâ deploy hedefi. `deploy_test.sh` Mac'ten `-test` dizinine scp yapar.

**Sorun:** MAIN 44 günde $1000 → $935 (−%6.5), panoda "WR %57.7" yazmasına rağmen para kaybediyordu.

**🔴 KÖK NEDEN 1 — Win-rate metriği ŞİŞİKTİ (ölçüm hatası):** `paper_bb.py` TP1'i **ayrı kapanış kaydı** olarak logluyor, ve `strategy.py`'de breakeven stop TRAILING dalında **hiç okunmuyor (ölü kod)** → TP1 sonrası bacak yapısal olarak kaybedemiyor. Her kazanan pozisyon **iki kazanan kayıt** üretiyordu. Kanıt: `TP1(13) == TP2(2)+TRAIL(11)` tam özdeşlik, TP1-sonrası 13 bacağın 0'ı negatif.
- Raporlanan WR %57.7 → **gerçek %43.6** · payoff 0.60 → 1.06 · başabaş WR **%48.2** · expectancy **−$0.76/pozisyon**
- Aynı hata backtest'i de şişirmişti: **PF 3.29 → havuzlanmış 1.95**

**🔴 KÖK NEDEN 2 — Trail çok dar:** `TRAIL_ATR=1.5` = stop genişliğinin %75'i → TP2'ye ulaşma %15.4. Kazançlar 0.73R'de kesiliyor, kayıplar 1.10R.

**Düzeltmeler (hepsi commit'li, VM'e deploy edildi):**
- **`metrics.py` (YENİ, tek doğruluk kaynağı):** pozisyon katlama + expectancy/payoff/başabaş-WR/R-multiple. `backtest.py`, `bench.py` (havuzlanmış), `paper_bb.py` hepsi bunu kullanıyor. Backtest verdict'i artık WR≥55 değil **expectancy+PF+MaxDD**'ye bakıyor.
- **Telemetri:** bar başına sinyal hunisi (`scanned/regime_pass/probe/confirm_ok/confirm_fail/full/blocked_max_open`) + `probe_cost` birikimi, state'te kalıcı.
- **Env knob'ları:** çıkış/confirm sabitleri `X_*` env ile sweep'e açık (`X_TRAIL_ATR`, `X_TP1_CLOSE_FRAC` vb.). **Varsayılanlar değişmedi** — regresyon testi kontrol arm'ını birebir üretiyor. TP1'deki hardcoded `0.50` artık `TP1_CLOSE_FRAC`.
- **Pano:** pozisyon-bazlı WR + payoff + expectancy + başabaş-WR gösteriyor; "counted per position" metodoloji notu eklendi. Ayrıca exporter regex bug'ı düzeltildi (`Bar #12,649` virgülü equity eğrisini bozuyordu).

**📊 Faz 5 deney sonuçları** (tam tablo: `BreakoutBot/BENCHMARKS.md`):

| Arm | 90g exp/R | 240g (derin ayı) exp/R |
|---|---|---|
| Kontrol | +0.228 | +0.057 |
| E2 `X_TRAIL_ATR=2.5` | +0.264 | +0.100 |
| **E6 `X_TP1_CLOSE_FRAC=0.0 X_TRAIL_ATR=2.5`** | **+0.281** | **+0.115** |
| E3 hacim teyidi 1.5× | +0.107 ⚠️ | — |

- **E6 her iki pencerede kazandı** (chop +%23, ayı **+%102**), MaxDD de iyileşti (−9.63→−8.69%), trade sayısı sabit → overfit değil.
- **Kazanan armın WR'si DÜŞÜK** (%56.7 vs %60.3), payoff'u yüksek (1.65 vs 1.28). Eski WR≥55 kriteriyle elenirdi.
- **Trail genişliği asıl darboğaz**, kısmi çıkış değil: E1 tek başına −%3, E2 tek başına +%16, ikisi birlikte **+%23** (süperadditif).
- **Hacim teyidini sıkılaştırmak zararlı** (−%53). Literatürün "kırılımda 2-3× hacim" tavsiyesi bu sisteme UYMUYOR.

**Canlı durum (29 Tem sonrası) — CANLI A/B TESTİ:**
| Servis | Konfig | Başlangıç | Mod |
|---|---|---|---|
| `breakoutbot` (MAIN) | kontrol | $935'ten devam | testnet emirleri |
| `breakoutbot-test` | **E6** (systemd `override.conf` env) | $1000 taze | lokal sim |

İkisinde de telemetri var, aynı piyasada paralel. **~12 Ağustos'ta değerlendir:** expectancy/R kıyasla (mutlak bakiye DEĞİL — farklı başlangıç). E6 canlıda da geçerse MAIN'e promote.

**⚠️ AÇIK SORU:** Düzeltilmiş metrikle bile **backtest +0.228R iken canlı −0.076R** — 0.30R uçurum ölçüm hatasıyla açıklanmıyor. Şüpheliler: dönem farkı, backtest'te intrabar iyimserliği (TP1 SL'den önce kontrol ediliyor), `MAX_OPEN=2` ve `EQUITY_THROTTLE_DD` backtest'te modellenmiyor. Telemetri bunu ölçecek (canlı confirm oranını backtest'in %14-31'iyle kıyasla).

**KURAL:** Karar metriği **expectancy/R**, kısıt **MaxDD**. Win rate hedef DEĞİL, sadece teşhis. Deneyler `bench.py` + sabit veri (`backtests/data/`) ile, tek seferde tek değişken; `backtest.py` her koşuda veri çeker → A/B için kullanma.

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

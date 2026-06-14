use crate::anomaly::detector::AnomalyDetector;
use crate::feeds::{KlineClose, PriceTick};
use crate::indicators::calculator::{
    calculate_adx, calculate_atr, calculate_bollinger_bands, calculate_macd, VwapCalculator,
};
use crate::{
    AnomalyData, CoreConfig, FundingData, IndicatorData, MarketSnapshot,
    MetaData, OrderbookData, PriceData, RegimeData, VolumeData,
};
use anyhow::Result;
use chrono::{Datelike, Utc};
use std::collections::{HashMap, VecDeque};
use tokio::sync::broadcast;
use tokio::time::{interval, Duration, Instant};
use tracing::{info, warn};

/// Rolling buffer of recent ticks for indicator calculation
struct TickBuffer {
    ticks: VecDeque<PriceTick>,
    max_size: usize,
    // Candle open prices (set/updated from kline data)
    candle_5m_open: f64,
    candle_15m_open: f64,
    candle_1h_open: f64,
    // 5-minute volume buckets for 24h avg (USDT volume)
    volume_history: VecDeque<f64>,
    // Current in-progress 5m bucket accumulator
    current_5m_bucket_id: u64,
    current_5m_bucket_vol: f64,
    // Close prices for indicator calculation (updated on kline close)
    close_prices: VecDeque<f64>,
    // OHLCV candles for ATR (updated on kline close)
    hlc_candles: VecDeque<(f64, f64, f64)>,
    // Session-based VWAP (resets at UTC midnight)
    vwap_calc: VwapCalculator,
    last_vwap_day: u32,
    // Anomaly detection
    anomaly_detector: AnomalyDetector,
    // Funding rate (updated periodically via REST)
    funding_rate: f64,
    // Open interest (updated periodically via REST)
    open_interest: f64,
    open_interest_usdt: f64,
}

impl TickBuffer {
    fn new() -> Self {
        Self {
            ticks: VecDeque::with_capacity(100_000),
            max_size: 100_000,
            candle_5m_open: 0.0,
            candle_15m_open: 0.0,
            candle_1h_open: 0.0,
            volume_history: VecDeque::with_capacity(288),
            current_5m_bucket_id: 0,
            current_5m_bucket_vol: 0.0,
            close_prices: VecDeque::with_capacity(1000),
            hlc_candles: VecDeque::with_capacity(200),
            vwap_calc: VwapCalculator::new(),
            last_vwap_day: 0,
            anomaly_detector: AnomalyDetector::new(),
            funding_rate: 0.0,
            open_interest: 0.0,
            open_interest_usdt: 0.0,
        }
    }

    // ── Historical warm-up ────────────────────────────────────────────────────

    /// Pre-populate buffers from REST klines so indicators are meaningful
    /// immediately on startup instead of needing ~30 minutes to warm up.
    fn warmup(&mut self, klines: &[KlineClose]) {
        if klines.is_empty() {
            return;
        }

        for kline in klines {
            self.close_prices.push_back(kline.close);
            if self.close_prices.len() > 1000 {
                self.close_prices.pop_front();
            }

            self.hlc_candles.push_back((kline.high, kline.low, kline.close));
            if self.hlc_candles.len() > 200 {
                self.hlc_candles.pop_front();
            }

            // Aggregate 1m kline volumes into 5m USDT buckets
            let bucket_id = kline.timestamp_ms / (5 * 60_000);
            let usdt_vol = kline.volume * kline.close;
            if bucket_id != self.current_5m_bucket_id {
                if self.current_5m_bucket_vol > 0.0 {
                    self.volume_history.push_back(self.current_5m_bucket_vol);
                    if self.volume_history.len() > 288 {
                        self.volume_history.pop_front();
                    }
                }
                self.current_5m_bucket_id = bucket_id;
                self.current_5m_bucket_vol = usdt_vol;
            } else {
                self.current_5m_bucket_vol += usdt_vol;
            }

            // Warm up VWAP with typical price × volume
            let typical = (kline.high + kline.low + kline.close) / 3.0;
            self.vwap_calc.update(typical, kline.volume);
        }

        // Set candle opens from the most recent period boundaries
        for kline in klines.iter().rev() {
            let minute = kline.timestamp_ms / 60_000;
            if self.candle_1h_open == 0.0 && minute % 60 == 0 {
                self.candle_1h_open = kline.open;
            }
            if self.candle_15m_open == 0.0 && minute % 15 == 0 {
                self.candle_15m_open = kline.open;
            }
            if self.candle_5m_open == 0.0 && minute % 5 == 0 {
                self.candle_5m_open = kline.open;
            }
            if self.candle_5m_open != 0.0
                && self.candle_15m_open != 0.0
                && self.candle_1h_open != 0.0
            {
                break;
            }
        }

        // Fallback: use the last kline's open if no boundary was found
        if let Some(last) = klines.last() {
            if self.candle_5m_open == 0.0 { self.candle_5m_open = last.open; }
            if self.candle_15m_open == 0.0 { self.candle_15m_open = last.open; }
            if self.candle_1h_open == 0.0 { self.candle_1h_open = last.open; }
        }
    }

    // ── Live kline update (called on every closed 1m candle) ─────────────────

    fn update_from_kline(&mut self, kline: &KlineClose) {
        // Update OHLCV buffers
        self.close_prices.push_back(kline.close);
        if self.close_prices.len() > 1000 {
            self.close_prices.pop_front();
        }

        self.hlc_candles.push_back((kline.high, kline.low, kline.close));
        if self.hlc_candles.len() > 200 {
            self.hlc_candles.pop_front();
        }

        // Accumulate 5m USDT volume bucket
        let bucket_id = kline.timestamp_ms / (5 * 60_000);
        let usdt_vol = kline.volume * kline.close;
        if bucket_id != self.current_5m_bucket_id {
            // New 5m period started — flush previous bucket
            if self.current_5m_bucket_vol > 0.0 {
                self.volume_history.push_back(self.current_5m_bucket_vol);
                if self.volume_history.len() > 288 {
                    self.volume_history.pop_front();
                }
            }
            self.current_5m_bucket_id = bucket_id;
            self.current_5m_bucket_vol = usdt_vol;
        } else {
            self.current_5m_bucket_vol += usdt_vol;
        }

        // Update candle opens at period boundaries
        let minute = kline.timestamp_ms / 60_000;
        if minute % 5 == 0 { self.candle_5m_open = kline.open; }
        if minute % 15 == 0 { self.candle_15m_open = kline.open; }
        if minute % 60 == 0 { self.candle_1h_open = kline.open; }

        // VWAP: typical price × volume
        let today = Utc::now().date_naive().day();
        if self.last_vwap_day != 0 && today != self.last_vwap_day {
            self.vwap_calc.reset();
        }
        self.last_vwap_day = today;
        let typical = (kline.high + kline.low + kline.close) / 3.0;
        self.vwap_calc.update(typical, kline.volume);
    }

    fn push(&mut self, tick: PriceTick) {
        // Feed the anomaly detector with every tick for flash crash detection
        self.anomaly_detector.record_price(tick.timestamp_ms, tick.price);

        if self.ticks.len() >= self.max_size {
            self.ticks.pop_front();
        }
        self.ticks.push_back(tick);
    }

    fn latest_price(&self) -> f64 {
        self.ticks.back().map(|t| t.price).unwrap_or(self.close_prices.back().copied().unwrap_or(0.0))
    }

    fn recent_volume(&self, window_ms: u64) -> f64 {
        let last_tick_time = self.ticks.back().map(|t| t.timestamp_ms).unwrap_or(0);
        if last_tick_time == 0 {
            // Fallback to 5m volume from warmup if no live ticks yet
            return self.volume_history.back().copied().unwrap_or(0.0);
        }
        self.ticks
            .iter()
            .rev()
            .take_while(|t| last_tick_time - t.timestamp_ms < window_ms)
            .map(|t| t.volume * t.price)
            .sum()
    }

    fn buy_sell_ratio(&self, window_ms: u64) -> f64 {
        let last_tick_time = self.ticks.back().map(|t| t.timestamp_ms).unwrap_or(0);
        if last_tick_time == 0 { return 0.5; }
        
        let recent: Vec<&PriceTick> = self.ticks
            .iter()
            .rev()
            .take_while(|t| last_tick_time - t.timestamp_ms < window_ms)
            .collect();

        let buy_vol: f64 = recent.iter()
            .filter(|t| !t.is_buyer_maker)
            .map(|t| t.volume * t.price)
            .sum();
        let total_vol: f64 = recent.iter()
            .map(|t| t.volume * t.price)
            .sum();

        if total_vol > 0.0 { buy_vol / total_vol } else { 0.5 }
    }
}

/// Calculate RSI from close prices
fn calculate_rsi(prices: &[f64], period: usize) -> f64 {
    if prices.len() < period + 1 {
        return 50.0; // Default neutral
    }

    let mut gains = 0.0;
    let mut losses = 0.0;
    let start = prices.len() - period - 1;

    for i in (start + 1)..prices.len() {
        let change = prices[i] - prices[i - 1];
        if change > 0.0 {
            gains += change;
        } else {
            losses += change.abs();
        }
    }

    let avg_gain = gains / period as f64;
    let avg_loss = losses / period as f64;

    if avg_loss == 0.0 {
        return 100.0;
    }

    let rs = avg_gain / avg_loss;
    100.0 - (100.0 / (1.0 + rs))
}

/// Calculate EMA
fn calculate_ema(prices: &[f64], period: usize) -> f64 {
    if prices.is_empty() {
        return 0.0;
    }
    if prices.len() < period {
        return prices.iter().sum::<f64>() / prices.len() as f64;
    }

    let multiplier = 2.0 / (period as f64 + 1.0);
    let mut ema = prices[..period].iter().sum::<f64>() / period as f64;

    for price in &prices[period..] {
        ema = (price - ema) * multiplier + ema;
    }
    ema
}

/// Build a MarketSnapshot from a TickBuffer for a given symbol
fn build_snapshot(
    symbol: &str,
    buffer: &mut TickBuffer,
    config: &CoreConfig,
    snapshot_counter: u64,
) -> Option<MarketSnapshot> {
    if buffer.ticks.is_empty() && buffer.close_prices.is_empty() {
        return None;
    }

    let start = Instant::now();

    let current_price = buffer.latest_price();
    let close_prices: Vec<f64> = buffer.close_prices.iter().copied().collect();
    let hlc_candles: Vec<(f64, f64, f64)> = buffer.hlc_candles.iter().copied().collect();

    // Calculate indicators
    let rsi = calculate_rsi(&close_prices, 14);
    let ema_9 = calculate_ema(&close_prices, 9);
    let ema_21 = calculate_ema(&close_prices, 21);
    let ema_50 = calculate_ema(&close_prices, 50);
    let macd = calculate_macd(&close_prices);
    let bb = calculate_bollinger_bands(&close_prices, 20, 2.0);
    let atr = calculate_atr(&hlc_candles, 14);
    let vwap = buffer.vwap_calc.vwap();
    let adx = calculate_adx(&hlc_candles, 14);

    let ema_alignment = if ema_9 > ema_21 && ema_21 > ema_50 {
        "BULLISH"
    } else if ema_9 < ema_21 && ema_21 < ema_50 {
        "BEARISH"
    } else {
        "MIXED"
    };

    // Volume analysis
    let vol_5m = buffer.recent_volume(300_000);
    let avg_vol = if buffer.volume_history.is_empty() {
        vol_5m
    } else {
        buffer.volume_history.iter().sum::<f64>() / buffer.volume_history.len() as f64
    };
    let volume_ratio = if avg_vol > 0.0 { vol_5m / avg_vol } else { 1.0 };
    let is_spike = volume_ratio > config.signals.volume_spike_threshold;

    // Trend detection
    let change_5m = if buffer.candle_5m_open > 0.0 {
        (current_price - buffer.candle_5m_open) / buffer.candle_5m_open * 100.0
    } else {
        0.0
    };
    let change_15m = if buffer.candle_15m_open > 0.0 {
        (current_price - buffer.candle_15m_open) / buffer.candle_15m_open * 100.0
    } else {
        0.0
    };
    let change_1h = if buffer.candle_1h_open > 0.0 {
        (current_price - buffer.candle_1h_open) / buffer.candle_1h_open * 100.0
    } else {
        0.0
    };

    // Trend: 5m is primary signal, 15m provides confirmation bonus via trend_strength
    // Use slightly higher threshold (0.15%) to filter micro-noise
    let trend = if change_5m > 0.15 { "UP" }
        else if change_5m < -0.15 { "DOWN" }
        else { "SIDEWAYS" };

    // Feed anomaly detector with buy/sell pressure data
    buffer.anomaly_detector.record_volume_bucket(vol_5m);
    buffer.anomaly_detector.record_buy_sell_ratio(
        buffer.buy_sell_ratio(300_000),
    );
    let anomaly_result = buffer.anomaly_detector.evaluate();

    let processing_ms = start.elapsed().as_millis() as u64;

    Some(MarketSnapshot {
        schema: "market_pulse_v1".to_string(),
        timestamp: Utc::now().to_rfc3339(),
        symbol: symbol.to_string(),
        exchange: config.exchange.primary.clone(),
        price: PriceData {
            current: current_price,
            open_5m: buffer.candle_5m_open,
            open_15m: buffer.candle_15m_open,
            open_1h: buffer.candle_1h_open,
            change_5m_pct: change_5m,
            change_15m_pct: change_15m,
            change_1h_pct: change_1h,
            trend: trend.to_string(),
            // Trend strength boosted when 15m agrees with 5m direction
            trend_strength: {
                let base = (change_5m.abs() / 1.0).min(0.8);
                let bonus = if (change_5m > 0.0 && change_15m > 0.0) || (change_5m < 0.0 && change_15m < 0.0) {
                    0.2  // 15m confirms 5m direction
                } else {
                    0.0
                };
                (base + bonus).min(1.0)
            },
        },
        volume: VolumeData {
            current_5m: vol_5m,
            avg_5m_24h: avg_vol,
            volume_ratio,
            is_spike,
            spike_magnitude: volume_ratio,
            buy_sell_ratio: buffer.buy_sell_ratio(300_000),
        },
        orderbook: OrderbookData {
            bid_depth_1pct: 0.0,  // TODO: separate orderbook feed
            ask_depth_1pct: 0.0,
            imbalance_ratio: 1.0,
            spread_bps: 0.0,
        },
        indicators: IndicatorData {
            rsi_14: rsi,
            rsi_signal: if rsi > 70.0 { "OVERBOUGHT".into() }
                else if rsi < 30.0 { "OVERSOLD".into() }
                else { "NEUTRAL".into() },
            macd_value: macd.value,
            macd_signal: macd.signal,
            macd_histogram: macd.histogram,
            bb_upper: bb.upper,
            bb_middle: bb.middle,
            bb_lower: bb.lower,
            bb_position_pct: bb.position_pct,
            ema_9,
            ema_21,
            ema_50,
            ema_alignment: ema_alignment.to_string(),
            atr_14: atr,
            vwap,
            adx_14: adx,
        },
        funding_rate: FundingData {
            current: buffer.funding_rate,
            predicted_next: 0.0,
            annualized_pct: buffer.funding_rate * 3.0 * 365.0 * 100.0, // 3 funding periods/day × 365 days
            signal: if buffer.funding_rate > 0.001 {
                "BEARISH_CROWDED_LONG".into()
            } else if buffer.funding_rate < -0.001 {
                "BULLISH_CROWDED_SHORT".into()
            } else if buffer.funding_rate > 0.0005 {
                "SLIGHTLY_BEARISH".into()
            } else if buffer.funding_rate < -0.0005 {
                "SLIGHTLY_BULLISH".into()
            } else {
                "NEUTRAL".into()
            },
            open_interest: buffer.open_interest,
            open_interest_usdt: buffer.open_interest_usdt,
        },
        anomaly_detection: AnomalyData {
            is_anomaly: anomaly_result.is_anomaly,
            anomaly_score: anomaly_result.score,
            liquidation_cascade_risk: anomaly_result
                .liquidation_cascade_risk
                .to_string(),
            detected_patterns: anomaly_result
                .patterns
                .iter()
                .map(|s| s.to_string())
                .collect(),
        },
        market_regime: RegimeData {
            current: if anomaly_result.liquidation_cascade_risk == "CRITICAL" {
                "CRASH".into()
            } else if is_spike && change_5m.abs() > 0.5 {
                "BREAKOUT".into()
            } else if change_5m > 0.1 {
                "TRENDING_UP".into()
            } else if change_5m < -0.1 {
                "TRENDING_DOWN".into()
            } else {
                "RANGING".into()
            },
            confidence: 0.5,
            volatility_regime: "MEDIUM".into(),
        },
        meta: MetaData {
            snapshot_id: format!(
                "snap_{}_{}_{}",
                symbol.to_lowercase(),
                Utc::now().format("%Y%m%d_%H%M%S"),
                snapshot_counter
            ),
            processing_latency_ms: processing_ms,
            data_freshness_ms: 0,
            active_feeds: vec!["binance_ws".into()],
            feed_health: "OK".into(),
        },
    })
}

/// Main signal processing loop — maintains per-symbol buffers and produces
/// independent snapshots for each configured symbol.
pub async fn run_processor(
    config: &CoreConfig,
    price_rx: &mut broadcast::Receiver<PriceTick>,
    kline_rx: &mut broadcast::Receiver<KlineClose>,
    snapshot_tx: broadcast::Sender<MarketSnapshot>,
) -> Result<()> {
    let mut buffers: HashMap<String, TickBuffer> = HashMap::new();

    // Create a buffer per symbol and warm up each from REST klines
    for symbol in &config.exchange.symbols {
        let mut buf = TickBuffer::new();
        match crate::feeds::binance::fetch_historical_klines(config, symbol, "1m", 500).await {
            Ok(klines) => {
                info!("[{}] Warming up buffers with {} klines", symbol, klines.len());
                buf.warmup(&klines);
                info!(
                    "[{}] Warm-up complete — close_prices: {}, hlc_candles: {}, vol_buckets: {}",
                    symbol,
                    buf.close_prices.len(),
                    buf.hlc_candles.len(),
                    buf.volume_history.len(),
                );
            }
            Err(e) => warn!("[{}] Historical kline fetch failed, starting cold: {}", symbol, e),
        }
        buffers.insert(symbol.clone(), buf);
    }

    let mut snapshot_interval = interval(Duration::from_secs(config.signals.snapshot_interval_sec));
    let mut funding_interval = interval(Duration::from_secs(60)); // Fetch funding + OI every 60s
    let mut snapshot_counter: u64 = 0;
    let symbols_for_funding: Vec<String> = config.exchange.symbols.clone();

    info!(
        "Signal processor started, interval: {}s, symbols: {:?}",
        config.signals.snapshot_interval_sec,
        config.exchange.symbols,
    );

    loop {
        tokio::select! {
            // Receive individual trade ticks — route to correct symbol buffer
            tick = price_rx.recv() => {
                match tick {
                    Ok(tick) => {
                        if let Some(buf) = buffers.get_mut(&tick.symbol) {
                            buf.push(tick);
                        }
                    }
                    Err(broadcast::error::RecvError::Lagged(n)) => {
                        warn!("Price tick channel lagged, skipped {} ticks", n);
                    }
                    Err(broadcast::error::RecvError::Closed) => break Ok(()),
                }
            }

            // Receive closed 1-minute candles — route to correct symbol buffer
            kline = kline_rx.recv() => {
                match kline {
                    Ok(kline) => {
                        if let Some(buf) = buffers.get_mut(&kline.symbol) {
                            buf.update_from_kline(&kline);
                        }
                    }
                    Err(broadcast::error::RecvError::Lagged(n)) => {
                        warn!("Kline channel lagged, skipped {} candles", n);
                    }
                    Err(broadcast::error::RecvError::Closed) => break Ok(()),
                }
            }

            // Fetch funding rate + open interest periodically via REST
            _ = funding_interval.tick() => {
                for symbol in &symbols_for_funding {
                    if let Some(buf) = buffers.get_mut(symbol) {
                        match crate::feeds::binance::fetch_funding_rate(config, symbol).await {
                            Ok(rate) => buf.funding_rate = rate,
                            Err(e) => warn!("[{}] Funding rate fetch failed: {}", symbol, e),
                        }
                        match crate::feeds::binance::fetch_open_interest(config, symbol).await {
                            Ok((oi, oi_usdt)) => {
                                buf.open_interest = oi;
                                buf.open_interest_usdt = oi_usdt;
                            }
                            Err(e) => warn!("[{}] Open interest fetch failed: {}", symbol, e),
                        }
                    }
                }
            }

            // Produce a snapshot for EACH symbol at every interval tick
            _ = snapshot_interval.tick() => {
                snapshot_counter += 1;

                for (symbol, buf) in buffers.iter_mut() {
                    if let Some(snapshot) = build_snapshot(symbol, buf, config, snapshot_counter) {
                        let _ = snapshot_tx.send(snapshot);
                    }
                }
            }
        }
    }
}

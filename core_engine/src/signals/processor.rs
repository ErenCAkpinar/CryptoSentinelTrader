use crate::feeds::PriceTick;
use crate::{
    AnomalyData, CoreConfig, FundingData, IndicatorData, MarketSnapshot,
    MetaData, OrderbookData, PriceData, RegimeData, VolumeData,
};
use anyhow::Result;
use chrono::Utc;
use std::collections::VecDeque;
use tokio::sync::broadcast;
use tokio::time::{interval, Duration, Instant};
use tracing::info;

/// Rolling buffer of recent ticks for indicator calculation
struct TickBuffer {
    ticks: VecDeque<PriceTick>,
    max_size: usize,
    // Aggregated OHLCV candles
    candle_5m_open: f64,
    candle_15m_open: f64,
    candle_1h_open: f64,
    volume_history: VecDeque<f64>,  // 5m volume buckets for 24h
    close_prices: VecDeque<f64>,    // For indicator calculation
}

impl TickBuffer {
    fn new() -> Self {
        Self {
            ticks: VecDeque::with_capacity(100_000),
            max_size: 100_000,
            candle_5m_open: 0.0,
            candle_15m_open: 0.0,
            candle_1h_open: 0.0,
            volume_history: VecDeque::with_capacity(288), // 24h of 5m buckets
            close_prices: VecDeque::with_capacity(1000),
        }
    }

    fn push(&mut self, tick: PriceTick) {
        if self.ticks.len() >= self.max_size {
            self.ticks.pop_front();
        }
        self.ticks.push_back(tick);
    }

    fn latest_price(&self) -> f64 {
        self.ticks.back().map(|t| t.price).unwrap_or(0.0)
    }

    fn recent_volume(&self, window_ms: u64) -> f64 {
        let now = self.ticks.back().map(|t| t.timestamp_ms).unwrap_or(0);
        self.ticks
            .iter()
            .rev()
            .take_while(|t| now - t.timestamp_ms < window_ms)
            .map(|t| t.volume * t.price)
            .sum()
    }

    fn buy_sell_ratio(&self, window_ms: u64) -> f64 {
        let now = self.ticks.back().map(|t| t.timestamp_ms).unwrap_or(0);
        let recent: Vec<&PriceTick> = self.ticks
            .iter()
            .rev()
            .take_while(|t| now - t.timestamp_ms < window_ms)
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

/// Main signal processing loop
pub async fn run_processor(
    config: &CoreConfig,
    price_rx: &mut broadcast::Receiver<PriceTick>,
    snapshot_tx: broadcast::Sender<MarketSnapshot>,
) -> Result<()> {
    let mut buffer = TickBuffer::new();
    let mut snapshot_interval = interval(Duration::from_secs(config.signals.snapshot_interval_sec));
    let mut snapshot_counter: u64 = 0;

    info!("Signal processor started, interval: {}s", config.signals.snapshot_interval_sec);

    loop {
        tokio::select! {
            // Receive ticks continuously
            tick = price_rx.recv() => {
                if let Ok(tick) = tick {
                    buffer.push(tick);
                }
            }

            // Produce snapshot at intervals
            _ = snapshot_interval.tick() => {
                if buffer.ticks.is_empty() {
                    continue;
                }

                let start = Instant::now();
                snapshot_counter += 1;

                let current_price = buffer.latest_price();
                let close_prices: Vec<f64> = buffer.close_prices.iter().copied().collect();

                // Calculate indicators
                let rsi = calculate_rsi(&close_prices, 14);
                let ema_9 = calculate_ema(&close_prices, 9);
                let ema_21 = calculate_ema(&close_prices, 21);
                let ema_50 = calculate_ema(&close_prices, 50);

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

                let trend = if change_5m > 0.1 { "UP" }
                    else if change_5m < -0.1 { "DOWN" }
                    else { "SIDEWAYS" };

                // Anomaly detection (simple z-score based)
                let anomaly_score = if is_spike { volume_ratio / 5.0 } else { 0.1 };
                let is_anomaly = anomaly_score > config.signals.anomaly_score_threshold;

                let processing_ms = start.elapsed().as_millis() as u64;

                let snapshot = MarketSnapshot {
                    schema: "market_pulse_v1".to_string(),
                    timestamp: Utc::now().to_rfc3339(),
                    symbol: config.exchange.symbols.first()
                        .cloned().unwrap_or_default(),
                    exchange: config.exchange.primary.clone(),
                    price: PriceData {
                        current: current_price,
                        open_5m: buffer.candle_5m_open,
                        open_15m: buffer.candle_15m_open,
                        open_1h: buffer.candle_1h_open,
                        change_5m_pct: change_5m,
                        change_15m_pct: 0.0,  // TODO
                        change_1h_pct: 0.0,   // TODO
                        trend: trend.to_string(),
                        trend_strength: (change_5m.abs() / 1.0).min(1.0),
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
                        macd_value: 0.0,      // TODO
                        macd_signal: 0.0,
                        macd_histogram: 0.0,
                        bb_upper: 0.0,        // TODO
                        bb_middle: 0.0,
                        bb_lower: 0.0,
                        bb_position_pct: 0.0,
                        ema_9,
                        ema_21,
                        ema_50,
                        ema_alignment: ema_alignment.to_string(),
                        atr_14: 0.0,          // TODO
                        vwap: 0.0,            // TODO
                    },
                    funding_rate: FundingData {
                        current: 0.0,         // TODO: separate API call
                        predicted_next: 0.0,
                        annualized_pct: 0.0,
                        signal: "UNKNOWN".into(),
                    },
                    anomaly_detection: AnomalyData {
                        is_anomaly,
                        anomaly_score,
                        liquidation_cascade_risk: "LOW".into(),
                        detected_patterns: vec![],
                    },
                    market_regime: RegimeData {
                        current: if is_spike && change_5m.abs() > 0.5 {
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
                            "snap_{}_{}",
                            Utc::now().format("%Y%m%d_%H%M%S"),
                            snapshot_counter
                        ),
                        processing_latency_ms: processing_ms,
                        data_freshness_ms: 0,
                        active_feeds: vec!["binance_ws".into()],
                        feed_health: "OK".into(),
                    },
                };

                // Store current close for next indicator calculation
                buffer.close_prices.push_back(current_price);
                if buffer.close_prices.len() > 1000 {
                    buffer.close_prices.pop_front();
                }

                let _ = snapshot_tx.send(snapshot);
            }
        }
    }
}

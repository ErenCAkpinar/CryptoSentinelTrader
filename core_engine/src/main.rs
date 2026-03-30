mod feeds;
mod signals;
mod indicators;
mod anomaly;

use anyhow::Result;
use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tokio::sync::broadcast;
use tracing::{info, warn, error};

/// Core market snapshot — produced every N seconds, consumed by Python AI engine
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarketSnapshot {
    pub schema: String,
    pub timestamp: String,
    pub symbol: String,
    pub exchange: String,
    pub price: PriceData,
    pub volume: VolumeData,
    pub orderbook: OrderbookData,
    pub indicators: IndicatorData,
    pub funding_rate: FundingData,
    pub anomaly_detection: AnomalyData,
    pub market_regime: RegimeData,
    pub meta: MetaData,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PriceData {
    pub current: f64,
    pub open_5m: f64,
    pub open_15m: f64,
    pub open_1h: f64,
    pub change_5m_pct: f64,
    pub change_15m_pct: f64,
    pub change_1h_pct: f64,
    pub trend: String,          // "UP", "DOWN", "SIDEWAYS"
    pub trend_strength: f64,    // 0.0 - 1.0
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VolumeData {
    pub current_5m: f64,
    pub avg_5m_24h: f64,
    pub volume_ratio: f64,
    pub is_spike: bool,
    pub spike_magnitude: f64,
    pub buy_sell_ratio: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderbookData {
    pub bid_depth_1pct: f64,
    pub ask_depth_1pct: f64,
    pub imbalance_ratio: f64,
    pub spread_bps: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndicatorData {
    pub rsi_14: f64,
    pub rsi_signal: String,
    pub macd_value: f64,
    pub macd_signal: f64,
    pub macd_histogram: f64,
    pub bb_upper: f64,
    pub bb_middle: f64,
    pub bb_lower: f64,
    pub bb_position_pct: f64,
    pub ema_9: f64,
    pub ema_21: f64,
    pub ema_50: f64,
    pub ema_alignment: String,  // "BULLISH", "BEARISH", "MIXED"
    pub atr_14: f64,
    pub vwap: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FundingData {
    pub current: f64,
    pub predicted_next: f64,
    pub annualized_pct: f64,
    pub signal: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnomalyData {
    pub is_anomaly: bool,
    pub anomaly_score: f64,
    pub liquidation_cascade_risk: String,
    pub detected_patterns: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegimeData {
    pub current: String,        // "TRENDING_UP", "TRENDING_DOWN", "RANGING", "BREAKOUT", "CRASH"
    pub confidence: f64,
    pub volatility_regime: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MetaData {
    pub snapshot_id: String,
    pub processing_latency_ms: u64,
    pub data_freshness_ms: u64,
    pub active_feeds: Vec<String>,
    pub feed_health: String,
}

/// Configuration loaded from config.toml
#[derive(Debug, Clone, Deserialize)]
pub struct CoreConfig {
    pub exchange: ExchangeConfig,
    pub signals: SignalConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ExchangeConfig {
    pub primary: String,
    pub symbols: Vec<String>,
    pub ws_url: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SignalConfig {
    pub snapshot_interval_sec: u64,
    pub volume_spike_threshold: f64,
    pub anomaly_score_threshold: f64,
}

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize logging
    tracing_subscriber::fmt()
        .with_env_filter("sentinel_core=info")
        .init();

    info!("🛡️ CryptoSentinel Core Engine starting...");

    // Broadcast channel: feeds → signal processor
    let (price_tx, _) = broadcast::channel::<feeds::PriceTick>(1024);
    // Broadcast channel: snapshots → Python AI engine (via ZMQ)
    let (snapshot_tx, _) = broadcast::channel::<MarketSnapshot>(64);

    // TODO: Load config from file
    let config = CoreConfig {
        exchange: ExchangeConfig {
            primary: "binance_futures_testnet".to_string(),
            symbols: vec!["BTCUSDT".to_string(), "ETHUSDT".to_string()],
            ws_url: "wss://fstream.binance.com/ws".to_string(),
        },
        signals: SignalConfig {
            snapshot_interval_sec: 10,
            volume_spike_threshold: 2.0,
            anomaly_score_threshold: 0.7,
        },
    };

    info!("Exchange: {}", config.exchange.primary);
    info!("Symbols: {:?}", config.exchange.symbols);
    info!("Snapshot interval: {}s", config.signals.snapshot_interval_sec);

    // Spawn tasks
    let config = Arc::new(config);

    // Task 1: WebSocket price feed
    let price_tx_clone = price_tx.clone();
    let config_clone = config.clone();
    let feed_handle = tokio::spawn(async move {
        if let Err(e) = feeds::binance::run_feed(&config_clone, price_tx_clone).await {
            error!("Feed error: {}", e);
        }
    });

    // Task 2: Signal processor (produces snapshots)
    let snapshot_tx_clone = snapshot_tx.clone();
    let config_clone = config.clone();
    let mut price_rx = price_tx.subscribe();
    let signal_handle = tokio::spawn(async move {
        if let Err(e) = signals::processor::run_processor(
            &config_clone,
            &mut price_rx,
            snapshot_tx_clone,
        ).await {
            error!("Signal processor error: {}", e);
        }
    });

    // Task 3: ZMQ publisher (sends snapshots to Python)
    let mut snapshot_rx = snapshot_tx.subscribe();
    let zmq_handle = tokio::spawn(async move {
        info!("ZMQ publisher starting on tcp://127.0.0.1:5555");
        // TODO: Implement ZMQ PUB socket
        loop {
            match snapshot_rx.recv().await {
                Ok(snapshot) => {
                    let json = serde_json::to_string(&snapshot).unwrap();
                    info!(
                        "📊 Snapshot {} | {} @ {} | RSI: {:.1} | Anomaly: {}",
                        snapshot.meta.snapshot_id,
                        snapshot.symbol,
                        snapshot.price.current,
                        snapshot.indicators.rsi_14,
                        snapshot.anomaly_detection.is_anomaly,
                    );
                    // TODO: zmq_socket.send(&json, 0);
                }
                Err(e) => {
                    warn!("Snapshot channel error: {}", e);
                }
            }
        }
    });

    info!("🛡️ CryptoSentinel Core Engine running. Press Ctrl+C to stop.");

    // Wait for all tasks
    tokio::select! {
        _ = feed_handle => warn!("Feed task ended"),
        _ = signal_handle => warn!("Signal processor ended"),
        _ = zmq_handle => warn!("ZMQ publisher ended"),
        _ = tokio::signal::ctrl_c() => info!("Shutdown signal received"),
    }

    info!("🛡️ CryptoSentinel Core Engine stopped.");
    Ok(())
}

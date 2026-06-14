mod feeds;
mod signals;
mod indicators;
mod anomaly;

use anyhow::Result;
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
    pub adx_14: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FundingData {
    pub current: f64,
    pub predicted_next: f64,
    pub annualized_pct: f64,
    pub signal: String,
    pub open_interest: f64,
    pub open_interest_usdt: f64,
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
    #[serde(default = "default_ws_url")]
    pub ws_url: String,
}

fn default_ws_url() -> String {
    "wss://fstream.binance.com/ws".to_string()
}

#[derive(Debug, Clone, Deserialize)]
pub struct SignalConfig {
    pub snapshot_interval_sec: u64,
    pub volume_spike_threshold: f64,
    pub anomaly_score_threshold: f64,
}

/// Load CoreConfig from config.toml file, falling back to defaults.
fn load_config() -> CoreConfig {
    let config_path = std::path::Path::new("../../config/config.toml");
    if config_path.exists() {
        match std::fs::read_to_string(config_path) {
            Ok(contents) => {
                match toml::from_str::<CoreConfig>(&contents) {
                    Ok(config) => {
                        println!("🛡️ [RustCore] Config loaded from {}", config_path.display());
                        return config;
                    }
                    Err(e) => {
                        println!("⚠️  [RustCore] Failed to parse config.toml: {} — using defaults", e);
                    }
                }
            }
            Err(e) => {
                println!("⚠️  [RustCore] Failed to read config.toml: {} — using defaults", e);
            }
        }
    } else {
        println!("⚠️  [RustCore] No config.toml found at {:?}, using defaults", config_path);
    }

    CoreConfig {
        exchange: ExchangeConfig {
            primary: "binance_futures_testnet".to_string(),
            symbols: vec!["BTCUSDT".to_string(), "ETHUSDT".to_string()],
            ws_url: default_ws_url(),
        },
        signals: SignalConfig {
            snapshot_interval_sec: 10,
            volume_spike_threshold: 2.0,
            anomaly_score_threshold: 0.7,
        },
    }
}

use std::io::{Write, stdout};

#[tokio::main]
async fn main() -> Result<()> {
    println!("🛡️ [RustCore] Starting Engine...");
    stdout().flush()?;
    
    let config = load_config();
    println!("🛡️ [RustCore] Config loaded. Exchange: {}, Symbols: {:?}", config.exchange.primary, config.exchange.symbols);
    stdout().flush()?;

    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter("sentinel_core=info")
        .init();

    info!("🛡️ CryptoSentinel Core Engine starting...");
    stdout().flush()?;

    // Broadcast channel: trade ticks → signal processor
    let (price_tx, _) = broadcast::channel::<feeds::PriceTick>(1024);
    // Broadcast channel: closed 1m candles → signal processor
    let (kline_tx, _) = broadcast::channel::<feeds::KlineClose>(256);
    // Broadcast channel: snapshots → Python AI engine (via ZMQ)
    let (snapshot_tx, _) = broadcast::channel::<MarketSnapshot>(64);

    let config = load_config();

    info!("Exchange: {}", config.exchange.primary);
    info!("Symbols: {:?}", config.exchange.symbols);
    info!("Snapshot interval: {}s", config.signals.snapshot_interval_sec);

    // Spawn tasks
    let config = Arc::new(config);

    // Task 1: WebSocket trade feed (buy/sell pressure, anomaly detection)
    let price_tx_clone = price_tx.clone();
    let config_clone = config.clone();
    let mut feed_handle = tokio::spawn(async move {
        if let Err(e) = feeds::binance::run_feed(&config_clone, price_tx_clone).await {
            error!("Trade feed error: {}", e);
        }
    });

    // Task 2: WebSocket kline feed (OHLCV candles for indicators)
    let kline_tx_clone = kline_tx.clone();
    let config_clone = config.clone();
    let mut kline_handle = tokio::spawn(async move {
        if let Err(e) = feeds::binance::run_kline_feed(&config_clone, kline_tx_clone).await {
            error!("Kline feed error: {}", e);
        }
    });

    // Task 3: Signal processor (warm-up + snapshot production)
    let snapshot_tx_clone = snapshot_tx.clone();
    let config_clone = config.clone();
    let mut price_rx = price_tx.subscribe();
    let mut kline_rx = kline_tx.subscribe();
    let mut signal_handle = tokio::spawn(async move {
        if let Err(e) = signals::processor::run_processor(
            &config_clone,
            &mut price_rx,
            &mut kline_rx,
            snapshot_tx_clone,
        ).await {
            error!("Signal processor error: {}", e);
        }
    });

    // Task 3: ZMQ publisher (sends snapshots to Python)
    //
    // zmq::Socket is not Send, so it cannot live inside a tokio task.
    // Solution: dedicate a std::thread for ZMQ and bridge via std::sync::mpsc.
    let (zmq_tx, zmq_rx) = std::sync::mpsc::channel::<String>();

    let zmq_thread = std::thread::spawn(move || {
        let ctx = zmq::Context::new();
        let socket = ctx.socket(zmq::PUB).expect("failed to create ZMQ PUB socket");
        socket
            .bind("tcp://127.0.0.1:5555")
            .expect("failed to bind ZMQ PUB to tcp://127.0.0.1:5555");
        info!("ZMQ PUB socket bound on tcp://127.0.0.1:5555");

        // Brief sleep so SUB sockets can connect before we publish
        std::thread::sleep(std::time::Duration::from_millis(200));

        for json in zmq_rx {
            if let Err(e) = socket.send(json.as_bytes(), 0) {
                warn!("ZMQ send error: {}", e);
            }
        }

        info!("ZMQ publisher thread exiting");
    });

    let mut snapshot_rx = snapshot_tx.subscribe();
    let mut zmq_handle = tokio::spawn(async move {
        loop {
            match snapshot_rx.recv().await {
                Ok(snapshot) => {
                    info!(
                        "📊 Snapshot {} | {} @ {} | RSI: {:.1} | Anomaly: {}",
                        snapshot.meta.snapshot_id,
                        snapshot.symbol,
                        snapshot.price.current,
                        snapshot.indicators.rsi_14,
                        snapshot.anomaly_detection.is_anomaly,
                    );
                    match serde_json::to_string(&snapshot) {
                        Ok(json) => {
                            if zmq_tx.send(json).is_err() {
                                error!("ZMQ bridge channel closed");
                                break;
                            }
                        }
                        Err(e) => error!("Snapshot serialization error: {}", e),
                    }
                }
                Err(broadcast::error::RecvError::Lagged(n)) => {
                    warn!("Snapshot channel lagged, skipped {} messages", n);
                }
                Err(broadcast::error::RecvError::Closed) => {
                    info!("Snapshot channel closed");
                    break;
                }
            }
        }
    });

    info!("🛡️ CryptoSentinel Core Engine running. Press Ctrl+C to stop.");

    // Wait for any task to finish or Ctrl+C
    tokio::select! {
        _ = &mut feed_handle => warn!("Trade feed task ended"),
        _ = &mut kline_handle => warn!("Kline feed task ended"),
        _ = &mut signal_handle => warn!("Signal processor ended"),
        _ = &mut zmq_handle => warn!("ZMQ publisher ended"),
        _ = tokio::signal::ctrl_c() => info!("Shutdown signal received"),
    }

    // Abort all tokio tasks so their owned senders/receivers are dropped.
    // This unblocks the ZMQ thread (zmq_rx iterator ends when zmq_tx is dropped).
    feed_handle.abort();
    kline_handle.abort();
    signal_handle.abort();
    zmq_handle.abort();

    let _ = zmq_thread.join();

    info!("🛡️ CryptoSentinel Core Engine stopped.");
    Ok(())
}

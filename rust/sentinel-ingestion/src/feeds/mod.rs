pub mod binance;

use serde::{Deserialize, Serialize};

/// Raw price tick from exchange WebSocket (@trade stream)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PriceTick {
    pub symbol: String,
    pub price: f64,
    pub volume: f64,
    pub timestamp_ms: u64,
    pub is_buyer_maker: bool,
    pub exchange: String,
}

/// A fully-closed 1-minute OHLCV candle from the @kline_1m stream (or REST warmup)
#[derive(Debug, Clone)]
pub struct KlineClose {
    pub symbol: String,
    /// Kline open time in milliseconds
    pub timestamp_ms: u64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    /// Base asset volume (e.g. BTC)
    pub volume: f64,
}

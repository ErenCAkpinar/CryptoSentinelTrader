pub mod binance;

use serde::{Deserialize, Serialize};

/// Raw price tick from exchange WebSocket
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PriceTick {
    pub symbol: String,
    pub price: f64,
    pub volume: f64,
    pub timestamp_ms: u64,
    pub is_buyer_maker: bool,
    pub exchange: String,
}

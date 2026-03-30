use super::PriceTick;
use crate::CoreConfig;
use anyhow::Result;
use futures_util::StreamExt;
use serde::Deserialize;
use tokio::sync::broadcast;
use tokio_tungstenite::connect_async;
use tracing::{info, warn, error};
use url::Url;

/// Binance WebSocket trade stream message
#[derive(Debug, Deserialize)]
struct BinanceTrade {
    #[serde(rename = "s")]
    symbol: String,
    #[serde(rename = "p")]
    price: String,
    #[serde(rename = "q")]
    quantity: String,
    #[serde(rename = "T")]
    trade_time: u64,
    #[serde(rename = "m")]
    is_buyer_maker: bool,
}

/// Connect to Binance Futures WebSocket and stream trades
pub async fn run_feed(
    config: &CoreConfig,
    tx: broadcast::Sender<PriceTick>,
) -> Result<()> {
    // Build combined stream URL: btcusdt@trade/ethusdt@trade
    let streams: Vec<String> = config
        .exchange
        .symbols
        .iter()
        .map(|s| format!("{}@trade", s.to_lowercase()))
        .collect();
    let stream_path = streams.join("/");

    let ws_url = format!("{}/{}", config.exchange.ws_url, stream_path);
    info!("Connecting to Binance WS: {}", ws_url);

    loop {
        match connect_async(Url::parse(&ws_url)?).await {
            Ok((ws_stream, _)) => {
                info!("✅ Binance WebSocket connected");
                let (_, mut read) = ws_stream.split();

                while let Some(msg) = read.next().await {
                    match msg {
                        Ok(tungstenite::Message::Text(text)) => {
                            if let Ok(trade) = serde_json::from_str::<BinanceTrade>(&text) {
                                let tick = PriceTick {
                                    symbol: trade.symbol,
                                    price: trade.price.parse().unwrap_or(0.0),
                                    volume: trade.quantity.parse().unwrap_or(0.0),
                                    timestamp_ms: trade.trade_time,
                                    is_buyer_maker: trade.is_buyer_maker,
                                    exchange: "binance_futures".to_string(),
                                };

                                // Non-blocking send — drops if no receivers
                                let _ = tx.send(tick);
                            }
                        }
                        Ok(tungstenite::Message::Ping(data)) => {
                            // Pong is handled automatically by tungstenite
                            let _ = data;
                        }
                        Err(e) => {
                            warn!("WebSocket message error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }

                warn!("WebSocket disconnected, reconnecting in 5s...");
            }
            Err(e) => {
                error!("WebSocket connection failed: {}", e);
            }
        }

        // Reconnect delay
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}
